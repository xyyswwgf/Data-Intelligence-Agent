"""API 集成测试：需要本地 PostgreSQL；LLM 调用全部 mock，不消耗 API 额度。"""

import io
import json
import uuid

import pytest

from backend import nl2sql
from backend.tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _auth(client, admin_headers):
    """本文件的用例都以管理员身份调用；权限相关用例见 test_kb_auth.py。"""
    global C
    C = _AuthedClient(client, admin_headers)


class _AuthedClient:
    def __init__(self, client, headers):
        self._client, self._headers = client, headers

    def __getattr__(self, method):
        func = getattr(self._client, method)

        def call(url, **kwargs):
            kwargs["headers"] = {**self._headers, **kwargs.get("headers", {})}
            return func(url, **kwargs)

        return call


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "healthy" and body["database"] == "ok"


def test_info_hides_system_tables():
    tables = [t["table_name"] for t in C.get("/api/info").json()["tables"]]
    assert "fact_orders" in tables
    assert not any(t.startswith("knowledge_") for t in tables)


def test_query_self_repairs_after_db_error(monkeypatch):
    calls = []

    def fake_generate(question, failed_sql=None, error=None, provider=None):
        calls.append((failed_sql, error))
        if failed_sql is None:
            return 'SELECT "no_such_column" FROM fact_orders'
        return 'SELECT COUNT(*) AS "订单数" FROM fact_orders'

    monkeypatch.setattr(nl2sql, "generate_sql", fake_generate)
    resp = C.post("/api/query", json={"question": "订单数"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attempts"] == 2 and body["columns"] == ["订单数"]
    assert "no_such_column" in calls[1][1]


def test_query_blocks_unsafe_sql(monkeypatch):
    monkeypatch.setattr(nl2sql, "generate_sql", lambda *a, **k: "DROP TABLE fact_orders")
    assert C.post("/api/query", json={"question": "x"}).status_code == 403


def test_query_empty_result_keeps_columns(monkeypatch):
    monkeypatch.setattr(nl2sql, "generate_sql", lambda *a, **k: "SELECT order_id FROM fact_orders WHERE 1 = 0")
    body = C.post("/api/query", json={"question": "x"}).json()
    assert body["columns"] == ["order_id"] and body["data"] == []


def test_readonly_transaction_is_enforced():
    from backend.database import execute_readonly_sql

    with pytest.raises(Exception, match="read-only"):
        execute_readonly_sql("CREATE TABLE should_not_exist (a int)")


def test_business_agent_end_to_end():
    body = C.post("/api/analyze", json={"question": "本月销售额为什么下降？"}).json()
    assert body["status"] == "completed", body["errors"]
    assert body["period"]["current"] > body["period"]["previous"]
    assert body["report"]["summary"] and len(body["charts"]) == 3
    history = C.get("/api/analysis/history").json()["items"]
    assert history[0]["run_id"] == body["run_id"]
    md = C.get(f"/api/analysis/{body['run_id']}/export?format=html")
    assert md.status_code == 200 and "<ul>" in md.text


def test_business_agent_respects_month_in_question():
    body = C.post("/api/analyze", json={"question": "2026年5月 GMV 怎么样"}).json()
    assert body["period"] == {"current": "2026-05", "previous": "2026-04"}


def _upload(path, content, **form):
    files = {"file": ("demo.csv", io.BytesIO(content), "text/csv")}
    return C.post(path, files=files, data={k: str(v) for k, v in form.items()})


def test_csv_import_analyze_and_delete():
    table = f"csv_test_{uuid.uuid4().hex[:8]}"
    content = (
        "销售明细\n"
        "日期,地区,类别,销售额,单号\n"
        + "".join(f"2026-0{m}-0{d},华{'北东'[d % 2]},{'AB'[m % 2]},{m * 100 + d},00{m}{d}\n" for m in range(1, 5) for d in range(1, 4))
        + "合计,,,,\n"
    ).encode("utf-8")

    preview = _upload("/api/import/csv/preview", content).json()
    assert preview["header_row"] == 2 and preview["auto_skip_trailing"] == 1
    assert preview["file_profile"]["recommended_primary_key"] == "单号"

    resp = _upload(
        "/api/import/csv",
        content,
        table_name=table,
        header_row=preview["header_row"],
        skip_trailing=preview["skip_trailing_rows"],
        primary_key_column="单号",
    )
    assert resp.status_code == 201, resp.text
    types = {c["name"]: c["type"] for c in resp.json()["columns"]}
    assert types["日期"] == "DATE" and types["单号"].startswith("VARCHAR")

    try:
        for question, intent in [
            ("按月份有什么变化", "trend"),
            ("哪个类别的数量最多", "group_compare"),
            ("哪些字段缺失", "missing"),
            ("有什么异常数据", "anomaly"),
        ]:
            body = C.post("/api/analyze/table", json={"table_name": table, "question": question}).json()
            assert body["status"] == "completed", body["errors"]
            assert body["plan"][1]["id"] == intent

        conflict = _upload("/api/import/csv", content, table_name=table, header_row=2, skip_trailing=1)
        assert conflict.status_code == 409

        bad = _upload(
            "/api/import/csv",
            content,
            table_name=table,
            overwrite="true",
            header_row=2,
            skip_trailing=1,
            column_types_override=json.dumps({"销售额": "INT); DROP TABLE fact_orders; --"}),
        )
        assert bad.status_code == 400
        # 失败的覆盖导入不能删掉原表
        assert table in [t["table_name"] for t in C.get("/api/tables?imported_only=true").json()["tables"]]
    finally:
        assert C.delete(f"/api/tables/{table}").status_code == 200


def test_cannot_delete_system_tables():
    assert C.delete("/api/tables/fact_orders").status_code == 403
