"""模型配置（本地 / 云端切换）、工具管理（HTTP / SQL 自定义工具）、技能管理测试。"""

import uuid

import httpx
import pytest

from backend.llm_gateway import LLMResponse, ToolCall, set_provider
from backend.tests.conftest import requires_db
from backend.tests.test_assistant import ScriptedProvider

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_provider(None)


@pytest.fixture(autouse=True)
def _cleanup_created_tools():
    """测试创建的自定义工具在用例结束后删除，避免残留进 Agent 的工具列表。"""
    from backend.database import execute_sql

    before = {r[0] for r in execute_sql("SELECT id FROM agent_tools")["data"]}
    yield
    after = {r[0] for r in execute_sql("SELECT id FROM agent_tools WHERE kind <> 'builtin'")["data"]}
    leftovers = sorted(after - before)
    if leftovers:
        execute_sql("DELETE FROM agent_tools WHERE id = ANY(:ids) RETURNING id", {"ids": leftovers})


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def test_secret_encryption_roundtrip():
    from backend.secrets_store import decrypt, encrypt, mask

    token = encrypt("sk-live-abcdefgh1234")
    assert token and "sk-live" not in token
    assert decrypt(token) == "sk-live-abcdefgh1234"
    assert mask("sk-live-abcdefgh1234") == "sk-****1234"
    assert encrypt("") is None and decrypt(None) == ""


def test_coerce_args_validates_types_enum_required():
    from backend.assistant.custom_tools import coerce_args, validate_schema

    schema = validate_schema({"type": "object", "properties": {
        "month": {"type": "string"}, "top": {"type": "integer"},
        "level": {"type": "string", "enum": ["省", "市"]}}, "required": ["month"]})
    assert coerce_args(schema, {"month": "2026-06", "top": "3", "x": 1}) == {"month": "2026-06", "top": 3}
    with pytest.raises(ValueError, match="缺少必填参数"):
        coerce_args(schema, {"top": 3})
    with pytest.raises(ValueError, match="应为 integer"):
        coerce_args(schema, {"month": "m", "top": "abc"})
    with pytest.raises(ValueError, match="只能是"):
        coerce_args(schema, {"month": "m", "level": "区"})


# ---------------------------------------------------------------------------
# 模型配置
# ---------------------------------------------------------------------------


def _profile(client, headers, **overrides):
    body = {"name": _uid("模型"), "kind": "local", "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1", "model": "qwen-test", **overrides}
    resp = client.post("/api/admin/models", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_model_profiles_crud_default_and_secrets(client, admin_headers, make_user):
    from backend.database import execute_sql
    from backend.llm_gateway import get_provider

    # 记录测试前的模型配置与默认项，结束后恢复，避免影响正在使用的环境
    existing = {p["id"] for p in client.get("/api/admin/models", headers=admin_headers).json()["items"]}
    original_default = next((p["id"] for p in client.get("/api/admin/models", headers=admin_headers).json()["items"]
                             if p["is_default"]), None)

    a = _profile(client, admin_headers)
    b = _profile(client, admin_headers, kind="cloud", base_url="https://api.example.com/v1", api_key="sk-secret-987654321")
    assert b["api_key_masked"] == "sk-****4321" and "api_key" not in b
    stored = execute_sql("SELECT api_key_encrypted FROM llm_profiles WHERE id = :i", {"i": b["id"]})["data"][0][0]
    assert "sk-secret" not in stored

    assert client.post(f"/api/admin/models/{b['id']}/default", headers=admin_headers).status_code == 200
    assert get_provider().profile_id == b["id"]
    assert get_provider().api_key == "sk-secret-987654321"  # 只在服务端解密
    assert get_provider(a["id"]).profile_id == a["id"]  # 按对话指定

    # 停用后再指定它：回退到默认模型，而不是报错
    client.patch(f"/api/admin/models/{a['id']}", headers=admin_headers, json={"enabled": False})
    assert get_provider(a["id"]).profile_id == b["id"]
    assert client.patch(f"/api/admin/models/{b['id']}", headers=admin_headers, json={"enabled": False}).status_code == 400

    # 编辑时不传 api_key 保持不变
    client.patch(f"/api/admin/models/{b['id']}", headers=admin_headers, json={"model": "deepseek-chat"})
    assert get_provider(b["id"]).api_key == "sk-secret-987654321"

    viewer = make_user("viewer")
    options = client.get("/api/models", headers=viewer).json()["items"]
    assert all("base_url" not in o and "api_key_masked" not in o for o in options)
    assert client.get("/api/admin/models", headers=viewer).status_code == 403

    # 删除默认模型后自动指定新的默认
    client.patch(f"/api/admin/models/{a['id']}", headers=admin_headers, json={"enabled": True})
    assert client.delete(f"/api/admin/models/{b['id']}", headers=admin_headers).status_code == 200
    assert any(p["is_default"] for p in client.get("/api/admin/models", headers=admin_headers).json()["items"])
    for p in client.get("/api/admin/models", headers=admin_headers).json()["items"]:
        if p["id"] not in existing:
            client.delete(f"/api/admin/models/{p['id']}", headers=admin_headers)
    if original_default:
        client.post(f"/api/admin/models/{original_default}/default", headers=admin_headers)


def test_model_profile_validation(client, admin_headers):
    resp = client.post("/api/admin/models", headers=admin_headers, json={
        "name": _uid("bad"), "provider": "openai_compatible", "base_url": "ftp://x.y", "model": "m"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

PROFIT_SQL = ("SELECT d.short_name AS \"门店\", SUM(s.gross_profit) AS profit FROM fact_store_sales s "
              "JOIN dim_store d ON d.store_id = s.store_id "
              "WHERE s.month_label = :month GROUP BY 1 ORDER BY 2 DESC LIMIT COALESCE(:top, 5)")


def _sql_tool(client, headers, **overrides):
    body = {"name": _uid("profit"), "display_name": "门店毛利", "description": "按月份查询各门店毛利排名", "kind": "sql",
            "parameters": {"type": "object", "properties": {"month": {"type": "string"}, "top": {"type": "integer"}},
                           "required": ["month"]},
            "config": {"sql": PROFIT_SQL}, **overrides}
    return client.post("/api/admin/tools", headers=headers, json=body)


def test_sql_tool_validation_execution_and_injection(client, admin_headers):
    assert _sql_tool(client, admin_headers, config={"sql": "DELETE FROM fact_store_sales"}).status_code == 400
    undeclared = _sql_tool(client, admin_headers, config={"sql": "SELECT * FROM fact_store_sales WHERE store_id = :store"})
    assert undeclared.status_code == 400 and "store" in undeclared.json()["detail"]
    assert _sql_tool(client, admin_headers, name="Bad-Name").status_code == 400
    assert _sql_tool(client, admin_headers, config={"sql": "SELECT * FROM users"}).status_code == 201  # 保存允许，执行时只读角色拦截

    tool = _sql_tool(client, admin_headers).json()
    ok = client.post(f"/api/admin/tools/{tool['id']}/test", headers=admin_headers, json={"args": {"month": "2026-08", "top": 2}}).json()
    assert ok["ok"] and ok["display"]["row_count"] == 2
    injected = client.post(f"/api/admin/tools/{tool['id']}/test", headers=admin_headers,
                           json={"args": {"month": "2026-08' OR '1'='1", "top": 2}}).json()
    assert injected["display"]["row_count"] == 0  # 参数被驱动绑定，注入无效
    missing = client.post(f"/api/admin/tools/{tool['id']}/test", headers=admin_headers, json={"args": {}}).json()
    assert not missing["ok"] and "month" in missing["for_model"]


def test_users_table_unreachable_from_sql_tool(client, admin_headers):
    tool = _sql_tool(client, admin_headers, config={"sql": "SELECT username FROM users"}).json()
    result = client.post(f"/api/admin/tools/{tool['id']}/test", headers=admin_headers, json={"args": {"month": "x"}}).json()
    assert not result["ok"] and "permission denied" in result["for_model"]


def test_http_tool_ssrf_guard_and_secret_headers(client, admin_headers, monkeypatch):
    from backend.assistant import custom_tools

    body = {"name": _uid("crm"), "display_name": "CRM 客户", "description": "按客户编号查询 CRM 客户信息", "kind": "http",
            "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
            "config": {"method": "GET", "url": "http://127.0.0.1:9/customers/{customer_id}", "response_path": "data"},
            "secret_headers": {"Authorization": "Bearer crm-token-123456"}}
    tool = client.post("/api/admin/tools", headers=admin_headers, json=body).json()
    assert tool["secret_headers"]["Authorization"] == "Bea****3456"
    blocked = client.post(f"/api/admin/tools/{tool['id']}/test", headers=admin_headers, json={"args": {"customer_id": "C1"}}).json()
    assert not blocked["ok"] and "内网地址" in blocked["for_model"]

    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json={"data": {"name": "华北客户", "level": "VIP"}})

    real_client = httpx.Client
    monkeypatch.setattr(custom_tools, "TOOL_HTTP_ALLOWED_HOSTS", {"127.0.0.1"})
    monkeypatch.setattr(custom_tools.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    ok = client.post(f"/api/admin/tools/{tool['id']}/test", headers=admin_headers, json={"args": {"customer_id": "C/1 x"}}).json()
    assert ok["ok"] and ok["display"]["http"]["json"] == {"name": "华北客户", "level": "VIP"}
    assert seen["url"].endswith("/customers/C%2F1%20x")  # 路径参数被 URL 编码，不能改写路径
    assert seen["auth"] == "Bearer crm-token-123456"

    bad_host = dict(body, name=_uid("bad"), config={"method": "GET", "url": "http://{host}/x"})
    assert client.post("/api/admin/tools", headers=admin_headers, json=bad_host).status_code == 400


def test_builtin_tools_toggle_and_role_filter(client, admin_headers):
    from backend.assistant.tools import load_registry
    from backend.database import execute_sql

    tools = client.get("/api/admin/tools", headers=admin_headers).json()["items"]
    run_sql = next(t for t in tools if t["name"] == "run_sql")
    assert client.delete(f"/api/admin/tools/{run_sql['id']}", headers=admin_headers).status_code == 400
    assert client.patch(f"/api/admin/tools/{run_sql['id']}", headers=admin_headers, json={"description": "修改内置工具的描述"}).status_code == 400
    try:
        client.patch(f"/api/admin/tools/{run_sql['id']}", headers=admin_headers, json={"enabled": False})
        assert "run_sql" not in {s.name for s in load_registry("admin", True, True).specs}
        client.patch(f"/api/admin/tools/{run_sql['id']}", headers=admin_headers, json={"enabled": True, "min_role": "editor"})
        assert "run_sql" not in {s.name for s in load_registry("viewer", True, True).specs}
        assert "run_sql" in {s.name for s in load_registry("editor", True, True).specs}
    finally:
        execute_sql("UPDATE agent_tools SET enabled = TRUE, min_role = 'viewer' WHERE name = 'run_sql' RETURNING id")


def test_agent_uses_custom_tool_and_cannot_call_disabled(client, admin_headers, make_user):
    from backend.assistant import sessions
    from backend.assistant.agent import run_turn

    tool = _sql_tool(client, admin_headers).json()
    viewer_headers = make_user("viewer")
    me = client.get("/api/auth/me", headers=viewer_headers).json()
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("a", tool["name"], {"month": "2026-08", "top": 3})]),
        LLMResponse(tool_calls=[ToolCall("b", "drop_everything", {})]),
        LLMResponse(text="城东第一"),
    ])
    set_provider(provider)
    sid = sessions.create_session(me["id"])["id"]
    events = list(run_turn(me, sid, "8 月毛利前三的门店"))
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["ok"] and results[0]["display"]["row_count"] == 3
    assert not results[1]["ok"] and results[1]["summary"] == "工具不可用"
    assert tool["name"] in provider.calls[0]["tools"]


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------


def test_skills_crud_and_progressive_loading(client, admin_headers, make_user):
    from backend.assistant import sessions
    from backend.assistant.agent import run_turn

    name = f"refund-{uuid.uuid4().hex[:5]}"
    assert client.post("/api/admin/skills", headers=admin_headers, json={
        "name": "Bad Name", "title": "x", "description": "用于测试的技能描述",
        "instructions": "这是一段足够长的技能指引内容"}).status_code == 400
    skill = client.post("/api/admin/skills", headers=admin_headers, json={
        "name": name, "title": "退款复盘", "description": "用户要做退款原因复盘时使用",
        "instructions": "第一步：先看退款率最高的品类。第二步：输出三条改进建议。", "tools": ["business_analysis"]}).json()
    viewer = make_user("viewer")
    assert name in [s["name"] for s in client.get("/api/skills", headers=viewer).json()["items"]]
    me = client.get("/api/auth/me", headers=viewer).json()

    # 模型根据目录自行加载技能：系统提示里只有简介，完整指引通过 use_skill 返回
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("s", "use_skill", {"name": name})]),
        lambda call: LLMResponse(text="已按技能执行") if "第一步：先看退款率" in call["messages"][-1]["content"]
        else LLMResponse(text="没有拿到技能指引"),
    ])
    set_provider(provider)
    sid = sessions.create_session(me["id"])["id"]
    events = list(run_turn(me, sid, "帮我复盘一下退款"))
    assert f"{name}（退款复盘）" in provider.calls[0]["system"]
    assert "第一步：先看退款率" not in provider.calls[0]["system"]
    assert [e for e in events if e["type"] == "done"][0]["content"] == "已按技能执行"

    # 用户手动指定技能：指引直接注入系统提示
    provider = ScriptedProvider([LLMResponse(text="好")])
    set_provider(provider)
    events = list(run_turn(me, sid, "开始", skill=name))
    assert "第一步：先看退款率" in provider.calls[0]["system"]
    assert any(e["type"] == "skill" for e in events)

    client.patch(f"/api/admin/skills/{skill['id']}", headers=admin_headers, json={"enabled": False})
    assert name not in [s["name"] for s in client.get("/api/skills", headers=viewer).json()["items"]]
    assert client.delete(f"/api/admin/skills/{skill['id']}", headers=admin_headers).status_code == 200


def test_python_analysis_tool_in_agent_and_role_default(client, admin_headers, make_user):
    from backend.assistant import sessions
    from backend.assistant.agent import run_turn
    from backend.assistant.tools import load_registry

    # 执行代码风险更高：默认仅编辑及以上可用
    assert "python_analysis" not in {s.name for s in load_registry("viewer", True, True).specs}
    assert "python_analysis" in {s.name for s in load_registry("editor", True, True).specs}

    editor = make_user("editor")
    me = client.get("/api/auth/me", headers=editor).json()
    sql = "SELECT product_type AS category, sale_price AS order_amount FROM fact_store_sales WHERE product_type IS NOT NULL"
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("p1", "python_analysis", {"sql": sql, "code": "df = pd.read_sql('x')"})]),
        lambda call: LLMResponse(tool_calls=[ToolCall("p2", "python_analysis", {
            "sql": sql, "code": "result = df.groupby('category')['order_amount'].sum().sort_values(ascending=False)"})])
        if "变量名就是 df" in call["messages"][-1]["content"] else LLMResponse(text="没有收到 df 提示"),
        LLMResponse(text="FD 最高"),
    ])
    set_provider(provider)
    sid = sessions.create_session(me["id"])["id"]
    events = list(run_turn(me, sid, "各产品线销售额排名"))
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["summary"] == "代码未通过安全校验"
    assert results[1]["ok"] and results[1]["display"]["columns"] == ["category", "order_amount"]
    assert results[1]["display"]["input_rows"] > 300
    assert [e for e in events if e["type"] == "done"][0]["content"] == "FD 最高"
