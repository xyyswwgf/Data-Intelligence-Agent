"""行业包、知识空间、流式输出、准确率评测。"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import text

from backend.llm_gateway import LLMResponse, OpenAICompatibleProvider, Provider, ToolCall, set_provider
from backend.tests.conftest import requires_db


class Scripted(Provider):
    """按顺序返回预设回复；stream_chat 用基类的退化实现（整段产出）。"""

    name = "scripted"
    model = "fake"

    def __init__(self, replies):
        self.replies = list(replies)

    def configured(self):
        return True

    def chat(self, system, messages, tools=None, max_tokens=1000, temperature=0.2, thinking=False):
        reply = self.replies.pop(0) if self.replies else LLMResponse(text="[]")
        return reply(messages) if callable(reply) else reply


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_provider(None)


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).json()


# ---------------------------------------------------------------------------
# 行业包
# ---------------------------------------------------------------------------


@requires_db
def test_industry_pack_switch(client, admin_headers, make_user):
    from backend.assistant.tools import load_registry

    info = client.get("/api/info", headers=admin_headers).json()
    assert info["pack"]["code"] == "retail_store" and info["pack"]["analysis_label"] == "经营分析"
    assert "business_analysis" in {s.name for s in load_registry("viewer", False, False).specs}

    viewer = make_user("viewer")
    assert client.put("/api/admin/packs/active", headers=viewer, json={"code": "generic"}).status_code == 403
    assert client.put("/api/admin/packs/active", headers=admin_headers, json={"code": "nope"}).status_code == 400
    try:
        assert client.put("/api/admin/packs/active", headers=admin_headers, json={"code": "generic"}).status_code == 200
        generic = client.get("/api/info", headers=admin_headers).json()["pack"]
        assert generic["analysis_label"] is None and not generic["store_import"]
        # 通用包没有门店专属分析：工具不提供给助手，「发现」里也不出现
        assert "business_analysis" not in {s.name for s in load_registry("viewer", False, False).specs}
        assert "business_analysis" not in {t["key"] for t in client.get("/api/market", headers=viewer).json()["tools"]}
        from backend.packs import active_pack

        assert "门店口径" not in active_pack().sql_rules
        sid = client.post("/api/chat/sessions", headers=viewer).json()["id"]
        resp = client.post(f"/api/chat/sessions/{sid}/messages", headers=viewer, json={"content": "经营怎么样", "mode": "agent"})
        assert "选择要分析的数据表" in resp.text
    finally:
        client.put("/api/admin/packs/active", headers=admin_headers, json={"code": "retail_store"})


def test_sql_prompt_formats_for_every_pack():
    from backend.llm_service import _SYSTEM_PROMPT_TEMPLATE
    from backend.packs import PACKS

    for pack in PACKS.values():
        prompt = _SYSTEM_PROMPT_TEMPLATE.format(ddl="CREATE TABLE t (a int);", coverage="x",
                                                domain_rules=pack.sql_rules, domain_examples=pack.sql_examples)
        assert pack.sql_rules in prompt
        assert ("fact_store_sales" in prompt) == (pack.code == "retail_store")


# ---------------------------------------------------------------------------
# 知识空间
# ---------------------------------------------------------------------------


@requires_db
def test_personal_space_is_private(client, make_user):
    alice, bob = make_user("viewer"), make_user("viewer")
    spaces = client.get("/api/kb/spaces", headers=alice).json()["items"]
    org = next(s for s in spaces if s["kind"] == "org")
    mine = next(s for s in spaces if s["kind"] == "personal")
    assert not org["can_write"] and mine["can_write"]  # 只读成员不能写企业空间，但可以写自己的空间

    marker = f"私密笔记{uuid.uuid4().hex[:6]}"
    assert client.post("/api/wiki/pages", headers=alice, json={"title": marker, "content_md": f"# {marker}\n\n采购底价 123"}).status_code == 403
    created = client.post("/api/wiki/pages", headers=alice, json={"title": marker, "content_md": f"# {marker}\n\n采购底价 123",
                                                                  "space_id": mine["id"]})
    assert created.status_code == 201, created.text
    page = created.json()

    from backend.kb.indexer import index_page

    index_page(page["id"])
    assert client.get(f"/api/wiki/pages/{page['slug']}", headers=alice).json()["space_kind"] == "personal"
    assert client.get(f"/api/wiki/pages/{page['slug']}", headers=bob).status_code == 404
    assert marker not in json.dumps(client.get("/api/wiki/tree", headers=bob).json(), ensure_ascii=False)
    assert marker not in json.dumps(client.get(f"/api/wiki/search?q={marker}", headers=bob).json(), ensure_ascii=False)
    assert marker in json.dumps(client.get(f"/api/wiki/search?q={marker}", headers=alice).json(), ensure_ascii=False)
    assert client.get(f"/api/wiki/tree?space_id={mine['id']}", headers=bob).status_code == 404
    assert client.put(f"/api/wiki/pages/{page['id']}", headers=bob, json={"base_version": 1, "content_md": "x"}).status_code == 404

    # 智能助手的知识检索同样按空间过滤
    from backend.assistant.tools import ToolContext, _search_knowledge

    bob_id, alice_id = _me(client, bob)["id"], _me(client, alice)["id"]
    assert marker not in _search_knowledge({"query": marker}, ToolContext(bob_id, None, False, None)).for_model
    assert marker in _search_knowledge({"query": marker}, ToolContext(alice_id, None, False, None)).for_model

    assert client.delete(f"/api/wiki/pages/{page['id']}", headers=alice).status_code == 200  # 自己的页面可以删


@requires_db
def test_editor_writes_org_space_and_upload_target(client, make_user):
    editor = make_user("editor")
    spaces = {s["kind"]: s for s in client.get("/api/kb/spaces", headers=editor).json()["items"]}
    assert spaces["org"]["can_write"] and not spaces["org"]["can_delete"]
    title = f"制度{uuid.uuid4().hex[:6]}"
    page = client.post("/api/wiki/pages", headers=editor, json={"title": title, "content_md": "# 制度"}).json()
    assert client.get(f"/api/wiki/pages/{page['slug']}", headers=editor).json()["space_kind"] == "org"
    assert client.delete(f"/api/wiki/pages/{page['id']}", headers=editor).status_code == 403  # 企业空间删除需要管理员

    doc = client.post("/api/kb/documents", headers=editor, data={"space_id": str(spaces["personal"]["id"])},
                      files={"file": (f"{title}.md", f"# {title} 个人文档".encode(), "text/markdown")})
    assert doc.status_code == 201, doc.text
    assert client.get(f"/api/wiki/pages/{doc.json()['page']['slug']}", headers=editor).json()["space_kind"] == "personal"
    other = make_user("editor")
    assert client.get(f"/api/kb/documents/{doc.json()['document_id']}/download", headers=other).status_code == 404


# ---------------------------------------------------------------------------
# 流式输出
# ---------------------------------------------------------------------------


@requires_db
def test_assistant_streams_answer_deltas(client, make_user):
    from backend.assistant import sessions
    from backend.assistant.agent import run_turn

    headers = make_user("viewer")
    user = _me(client, headers)
    set_provider(Scripted([
        LLMResponse(text="先查一下", tool_calls=[ToolCall("c1", "list_tables", {})]),
        LLMResponse(text="一共有这些数据表。"),
    ]))
    sid = sessions.create_session(user["id"])["id"]
    events = list(run_turn(user, sid, "有哪些表？", remember=False))
    deltas = [e for e in events if e["type"] == "delta"]
    assert [d["text"] for d in deltas] == ["先查一下", "一共有这些数据表。"]
    assert deltas[0]["step"] == 1 and deltas[1]["step"] == 2 and deltas[1]["channel"] == "answer"
    assert events[-1]["type"] == "done" and events[-1]["content"] == "一共有这些数据表。"
    stored = sessions.get_messages(sid)[-1]
    assert not any(e.get("type") == "delta" for e in stored["trace"])  # 草稿不落库


def _sse(*chunks):
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_openai_stream_parsing(monkeypatch):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "stream_options" in body:
            return httpx.Response(400, text="unknown field stream_options")  # 模拟不支持的推理服务
        return httpx.Response(200, text=_sse(
            {"choices": [{"delta": {"reasoning_content": "想一想"}}]},
            {"choices": [{"delta": {"content": "好"}}]},
            {"choices": [{"delta": {"content": "的"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "function": {"name": "run_sql", "arguments": "{\"sql\": "}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"SELECT 1\"}"}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
        ), headers={"content-type": "text/event-stream"})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    provider = OpenAICompatibleProvider("k", "http://llm.test/v1", "m")
    stream = provider.stream_chat("sys", [{"role": "user", "content": "hi"}])
    pieces = []
    while True:
        try:
            pieces.append(next(stream))
        except StopIteration as stop:
            resp = stop.value
            break
    assert pieces == [("reasoning", "想一想"), ("text", "好"), ("text", "的")]
    assert resp.text == "好的" and resp.reasoning == "想一想" and resp.stop_reason == "tool_calls"
    assert resp.tool_calls[0].name == "run_sql" and resp.tool_calls[0].arguments == {"sql": "SELECT 1"}
    assert resp.input_tokens == 12 and resp.output_tokens == 5
    assert "stream_options" in seen[0] and "stream_options" not in seen[1]  # 400 后去掉 stream_options 重试


# ---------------------------------------------------------------------------
# 准确率评测
# ---------------------------------------------------------------------------


def test_number_extraction_and_tolerance():
    from backend.evals.runner import close, extract_numbers

    nums = extract_numbers("销售额 25.41万元，毛利 19,713.98 元，毛利率 8.3%，共 48 台")
    assert any(close(n, 254_055.14) for n in nums)  # 万元写法四舍五入
    assert any(close(n, 19713.98) for n in nums)
    assert any(close(n, 0.0829) for n in nums)  # 百分比
    assert any(close(n, 48) for n in nums) and not any(close(n, 47) for n in nums)


@requires_db
def test_eval_run_scores_answers(client, admin_headers):
    from backend.database import get_connection
    from backend.evals.runner import create_run, execute_run, get_set

    eval_set = get_set("retail_store")
    case = next(c for c in eval_set["cases"] if c["id"] == "phones_total")
    with get_connection() as conn:
        expected = conn.execute(text(case["checks"][0]["sql"])).scalar()
    set_provider(Scripted([LLMResponse(text=f"最新一个月一共卖了 {expected} 台手机。"),
                           LLMResponse(text="大约 1 台。")]))
    admin_id = _me(client, admin_headers)["id"]
    run_id = create_run("retail_store", None, admin_id, ["phones_total", "phones_total"])
    run = execute_run(run_id)
    assert run["status"] == "done" and run["total"] == 1 and run["passed"] == 1 and run["accuracy"] == 1.0

    set_provider(Scripted([LLMResponse(text="大约 1 台。")]))
    wrong = execute_run(create_run("retail_store", None, admin_id, ["phones_total"]))
    assert wrong["passed"] == 0 and wrong["results"][0]["checks"][0]["expected"] == expected

    listed = client.get("/api/admin/evals/runs", headers=admin_headers).json()["items"]
    assert listed[0]["id"] == wrong["id"] and listed[0]["accuracy"] == 0
    sets = client.get("/api/admin/evals/sets", headers=admin_headers).json()["items"]
    assert any(s["code"] == "retail_store" and len(s["cases"]) >= 15 for s in sets)
    assert client.get(f"/api/admin/evals/runs/{run_id}", headers=admin_headers).json()["results"][0]["passed"]
