"""认证、权限、Wiki、文档入库、检索问答的 API 集成测试（需要 PostgreSQL + pgvector）。"""

import io
import uuid


from backend.tests.conftest import requires_db

pytestmark = requires_db


def _md(name, body):
    return {"file": (name, io.BytesIO(body.encode("utf-8")), "text/markdown")}


def test_requires_login(client):
    assert client.get("/api/info").status_code == 401
    assert client.get("/api/wiki/tree").status_code == 401
    assert client.get("/api/wiki/tree", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_lockout(client, admin_headers):
    username = f"lock_{uuid.uuid4().hex[:6]}"
    client.post("/api/admin/users", headers=admin_headers,
                json={"username": username, "password": "Passw0rd123", "role": "viewer"})
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": username, "password": "wrong"}).status_code == 401
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd123"})
    assert resp.status_code == 401 and "锁定" in resp.json()["detail"]


def test_role_permissions(client, make_user):
    viewer, editor = make_user("viewer"), make_user("editor")
    assert client.get("/api/wiki/tree", headers=viewer).status_code == 200
    assert client.post("/api/wiki/pages", headers=viewer, json={"title": "x"}).status_code == 403
    assert client.post("/api/kb/documents", headers=viewer, files=_md("a.md", "# a")).status_code == 403
    assert client.get("/api/admin/users", headers=editor).status_code == 403
    created = client.post("/api/wiki/pages", headers=editor, json={"title": f"编辑页 {uuid.uuid4().hex[:4]}"})
    assert created.status_code == 201
    assert client.delete(f"/api/wiki/pages/{created.json()['id']}", headers=editor).status_code == 403


def test_llm_sql_cannot_read_system_tables(client, admin_headers, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "generate_sql", lambda *a, **k: "SELECT username, password_hash FROM users")
    resp = client.post("/api/query", headers=admin_headers, json={"question": "列出所有用户密码"})
    assert resp.status_code == 500 and "permission denied" in resp.json()["detail"]


def test_wiki_lifecycle(client, admin_headers):
    title = f"测试页面 {uuid.uuid4().hex[:6]}"
    page = client.post("/api/wiki/pages", headers=admin_headers,
                       json={"title": title, "content_md": "# 标题\n\n第一版内容", "tags": ["测试", "测试", " "]}).json()
    detail = client.get(f"/api/wiki/pages/{page['slug']}", headers=admin_headers).json()
    assert detail["tags"] == ["测试"] and detail["index_status"] == "ready" and detail["chunk_count"] >= 1

    child = client.post("/api/wiki/pages", headers=admin_headers, json={
        "title": f"子页面 {uuid.uuid4().hex[:6]}", "parent_id": page["id"],
        "content_md": f"引用 [[{title}]] 和 [[不存在的页面]]",
    }).json()
    detail = client.get(f"/api/wiki/pages/{page['slug']}", headers=admin_headers).json()
    assert [c["slug"] for c in detail["children"]] == [child["slug"]]
    assert [b["slug"] for b in detail["backlinks"]] == [child["slug"]]
    child_detail = client.get(f"/api/wiki/pages/{child['slug']}", headers=admin_headers).json()
    assert child_detail["breadcrumbs"][-1]["slug"] == page["slug"]
    assert child_detail["missing_links"] == ["不存在的页面"]

    # 不能把父页面移到自己的子页面下
    bad_move = client.put(f"/api/wiki/pages/{page['id']}", headers=admin_headers,
                          json={"base_version": 1, "parent_id": child["id"], "move": True})
    assert bad_move.status_code == 400

    v2 = client.put(f"/api/wiki/pages/{page['id']}", headers=admin_headers,
                    json={"base_version": 1, "content_md": "# 标题\n\n第二版", "change_note": "改写"})
    assert v2.status_code == 200 and v2.json()["version"] == 2
    stale = client.put(f"/api/wiki/pages/{page['id']}", headers=admin_headers, json={"base_version": 1, "content_md": "覆盖"})
    assert stale.status_code == 409

    versions = client.get(f"/api/wiki/pages/{page['id']}/versions", headers=admin_headers).json()["items"]
    assert [v["version"] for v in versions] == [2, 1]
    restored = client.post(f"/api/wiki/pages/{page['id']}/versions/1/restore", headers=admin_headers).json()
    assert restored["version"] == 3
    assert "第一版" in client.get(f"/api/wiki/pages/{page['slug']}", headers=admin_headers).json()["content_md"]

    assert client.delete(f"/api/wiki/pages/{page['id']}", headers=admin_headers).status_code == 200
    assert client.get(f"/api/wiki/pages/{page['slug']}", headers=admin_headers).status_code == 404
    # 子页面上移到根目录
    assert client.get(f"/api/wiki/pages/{child['slug']}", headers=admin_headers).json()["parent_id"] is None
    client.delete(f"/api/wiki/pages/{child['id']}", headers=admin_headers)


def test_document_upload_search_and_ask(client, admin_headers):
    marker = uuid.uuid4().hex[:8]
    body = f"# 差旅制度 {marker}\n\n## 住宿标准\n\n一线城市住宿标准为每晚 600 元。编号{marker}\n\n## 报销时限\n\n30 天内提交。\n"
    resp = client.post("/api/kb/documents", headers=admin_headers, files=_md(f"travel_{marker}.md", body),
                       data={"tags": "制度,差旅"})
    assert resp.status_code == 201, resp.text
    slug = resp.json()["page"]["slug"]
    page = client.get(f"/api/wiki/pages/{slug}", headers=admin_headers).json()
    assert set(page["tags"]) >= {"制度", "差旅", "MD"} and page["document_filename"] == f"travel_{marker}.md"

    dup = client.post("/api/kb/documents", headers=admin_headers, files=_md("again.md", body))
    assert dup.status_code == 409 and dup.json()["detail"]["page_slug"] == slug
    bad = client.post("/api/kb/documents", headers=admin_headers,
                      files={"file": ("a.xlsx", io.BytesIO(b"x"), "application/octet-stream")})
    assert bad.status_code == 422

    download = client.get(f"/api/kb/documents/{page['document_id']}/download", headers=admin_headers)
    assert download.status_code == 200 and marker in download.text

    found = client.get("/api/wiki/search", params={"q": f"住宿标准 {marker}"}, headers=admin_headers).json()
    assert found["pages"][0]["slug"] == slug

    answer = client.post("/api/kb/ask", headers=admin_headers, json={"question": f"{marker} 一线城市住宿标准是多少"}).json()
    assert answer["citations"][0]["slug"] == slug
    assert answer["generation"] in {"llm", "extractive"} and "600" in answer["answer"]

    nothing = client.post("/api/kb/ask", headers=admin_headers, json={"question": "量子纠缠火星殖民计划"}).json()
    assert nothing["citations"] == [] and "没有找到" in nothing["answer"]

    index = client.get("/api/wiki/index", headers=admin_headers).json()
    assert index["stats"]["documents"] >= 1 and any(t["tag"] == "差旅" for t in index["tags"])
    client.delete(f"/api/wiki/pages/{page['id']}", headers=admin_headers)


def test_history_is_private_per_user(client, make_user):
    alice, bob = make_user("viewer"), make_user("viewer")
    run = client.post("/api/analyze", headers=alice, json={"question": "本月销售额为什么下降？"}).json()
    assert run["status"] == "completed"
    assert run["run_id"] in [i["run_id"] for i in client.get("/api/analysis/history", headers=alice).json()["items"]]
    assert run["run_id"] not in [i["run_id"] for i in client.get("/api/analysis/history", headers=bob).json()["items"]]
    assert client.get(f"/api/analysis/{run['run_id']}", headers=bob).status_code == 404


def test_audit_log_records_actions(client, admin_headers):
    logs = client.get("/api/admin/audit", headers=admin_headers, params={"action": "login"}).json()
    assert logs["total"] >= 1 and logs["items"][0]["action"] == "login"
