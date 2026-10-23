"""推理 Agent、记忆、上下文管理与会话 API 测试。大模型用脚本化的假 Provider 代替，结果确定、不消耗额度。"""

import json
import uuid

import pytest

from backend.llm_gateway import LLMResponse, Provider, ToolCall, set_provider
from backend.tests.conftest import requires_db

pytestmark = requires_db


class ScriptedProvider(Provider):
    """按顺序返回预设的回复，并记录每次调用收到的消息，便于断言工具结果是否回传给模型。"""

    name = "scripted"
    model = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def configured(self):
        return True

    def chat(self, system, messages, tools=None, max_tokens=1000, temperature=0.2, thinking=False):
        self.calls.append({"system": system, "messages": list(messages), "tools": [t.name for t in tools or []]})
        if not self.replies:
            return LLMResponse(text="[]")  # 后台记忆抽取等额外调用
        reply = self.replies.pop(0)
        return reply(self.calls[-1]) if callable(reply) else reply


class Unconfigured(Provider):
    name = "none"

    def configured(self):
        return False


@pytest.fixture
def user(client, make_user):
    headers = make_user("viewer")
    me = client.get("/api/auth/me", headers=headers).json()
    return {**me, "headers": headers}


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_provider(None)


def _run(user, question, session_id=None, table=None):
    from backend.assistant import sessions
    from backend.assistant.agent import run_turn

    sid = session_id or sessions.create_session(user["id"])["id"]
    events = list(run_turn(user, sid, question, table))
    return sid, events


def _of(events, kind):
    return [e for e in events if e["type"] == kind]


def test_react_loop_self_corrects_sql_and_verifies_citations(user):
    provider = ScriptedProvider([
        LLMResponse(reasoning="先查表结构", tool_calls=[ToolCall("c1", "run_sql", {"sql": 'SELECT "no_col" FROM fact_orders'})]),
        # 第二步：模型应该看到上一步的报错并修正
        lambda call: LLMResponse(tool_calls=[ToolCall("c2", "run_sql", {"sql": 'SELECT COUNT(*) AS "订单数" FROM fact_orders'})])
        if "no_col" in call["messages"][-1]["content"] else LLMResponse(text="没有看到报错"),
        LLMResponse(text="共有 7190 笔订单 [[不存在的页面]]。"),
    ])
    set_provider(provider)
    sid, events = _run(user, "一共有多少订单？")

    results = _of(events, "tool_result")
    assert [r["ok"] for r in results] == [False, True]
    assert results[1]["display"]["columns"] == ["订单数"]
    assert _of(events, "thinking")[0]["text"] == "先查表结构"
    done = _of(events, "done")[0]
    assert done["content"] == "共有 7190 笔订单。"  # 编造的引用被去掉
    assert _of(events, "context")[0]["parts"]["question"] > 0

    from backend.assistant.sessions import get_messages

    messages = get_messages(sid)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert [e["type"] for e in messages[1]["trace"] if e["type"] == "tool_call"] == ["tool_call", "tool_call"]
    assert messages[1]["payload"]["usage"]["steps"] == 3


def test_step_limit_forces_final_answer(user, monkeypatch):
    from backend.assistant import agent

    monkeypatch.setattr(agent, "AGENT_MAX_STEPS", 2)
    loop = LLMResponse(tool_calls=[ToolCall("x", "list_tables", {})])
    provider = ScriptedProvider([loop, loop, LLMResponse(text="基于已有信息的回答")])
    set_provider(provider)
    _, events = _run(user, "随便查查")
    summaries = [r["summary"] for r in _of(events, "tool_result")]
    assert summaries[1] == "重复调用已跳过"
    assert provider.calls[-1]["tools"] == []  # 最后一轮不再提供工具，强制作答
    assert _of(events, "done")[0]["content"] == "基于已有信息的回答"


def test_rule_fallback_without_llm(user):
    set_provider(Unconfigured())
    _, events = _run(user, "本月销售额为什么下降？")
    assert _of(events, "tool_call")[0]["name"] == "business_analysis"
    done = _of(events, "done")[0]
    assert "GMV" in done["content"]


def test_explicit_memory_and_recall_into_context(user):
    from backend.assistant import memory

    set_provider(Unconfigured())
    _, events = _run(user, "记住：我负责华东区域的渠道运营")
    assert _of(events, "memory")[0]["memory"]["content"] == "我负责华东区域的渠道运营"
    assert "已经记住" in _of(events, "done")[0]["content"]

    # 相似内容再次写入时合并而不是新增
    again = memory.save_memory(user["id"], "我负责华东区域的渠道运营工作", "fact", "explicit")
    assert again["action"] == "updated"
    memory.save_memory(user["id"], "报告一律用表格呈现", "preference", "manual", pinned=True)

    provider = ScriptedProvider([LLMResponse(text="好的")])
    set_provider(provider)
    _run(user, "华东渠道最近怎么样")
    system = provider.calls[0]["system"]
    assert "报告一律用表格呈现" in system and "核心记忆" in system
    assert "华东区域" in system


def test_memory_disabled_hides_tool_and_recall(user, client):
    from backend.assistant import memory

    memory.save_memory(user["id"], "用户喜欢折线图", "preference", "manual", pinned=True)
    assert client.put("/api/memories/settings", headers=user["headers"], json={"enabled": False}).json() == {"enabled": False}
    provider = ScriptedProvider([LLMResponse(text="好")])
    set_provider(provider)
    _run(user, "随便聊聊")
    assert "remember" not in provider.calls[0]["tools"]
    assert "用户喜欢折线图" not in provider.calls[0]["system"]
    client.put("/api/memories/settings", headers=user["headers"], json={"enabled": True})


def test_context_budget_trims_old_history(user):
    from backend.assistant import sessions
    from backend.assistant.context import build_context

    sid = sessions.create_session(user["id"])["id"]
    for i in range(12):
        sessions.add_message(sid, "user", f"问题{i} " + "数据" * 300)
        sessions.add_message(sid, "assistant", f"回答{i} " + "结论" * 300)
    session = sessions.get_session(user["id"], sid)
    ctx = build_context("系统指令", [], session, user["id"], "最新的问题", use_memory=False, window=5000)
    assert ctx.dropped_messages > 0
    assert ctx.messages[0]["role"] == "user"
    assert ctx.messages[-1]["content"] == "最新的问题"
    assert ctx.messages[-2]["content"].startswith("回答11")  # 最近一轮一定保留
    assert ctx.total() <= 5000


def test_compaction_folds_old_messages_into_summary(user):
    from backend.assistant import sessions

    from backend.llm_gateway import ModelSettings

    tiny = Unconfigured()  # 无模型时使用截断式摘要降级
    tiny.settings = ModelSettings(context_window=2000)
    set_provider(tiny)
    sid = sessions.create_session(user["id"])["id"]
    for i in range(10):
        sessions.add_message(sid, "user", f"第{i}个问题 " + "内容" * 120)
        sessions.add_message(sid, "assistant", f"第{i}个回答 " + "内容" * 120)
    assert sessions.maybe_compact(sid) is True
    session = sessions.get_session(user["id"], sid)
    assert "第0个问题" in session["summary"]
    assert session["summary_upto"] == 20 - sessions.CONTEXT_KEEP_RECENT


def _sse_events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_chat_api_streams_and_isolates_sessions(client, user, make_user):
    set_provider(Unconfigured())
    sid = client.post("/api/chat/sessions", headers=user["headers"]).json()["id"]
    resp = client.post(f"/api/chat/sessions/{sid}/messages", headers=user["headers"],
                       json={"content": "差旅住宿标准是什么", "mode": "kb"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp)
    assert events[0]["type"] == "session" and events[-1]["type"] == "done"

    resp = client.post(f"/api/chat/sessions/{sid}/messages", headers=user["headers"],
                       json={"content": "本月销售额为什么下降？", "mode": "assistant"})
    assert _sse_events(resp)[-1]["type"] == "done"

    detail = client.get(f"/api/chat/sessions/{sid}", headers=user["headers"]).json()
    assert detail["title"] == "差旅住宿标准是什么"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["mode"] == "kb"
    assert sid in [s["id"] for s in client.get("/api/chat/sessions", headers=user["headers"]).json()["items"]]

    other = make_user("viewer")
    assert client.get(f"/api/chat/sessions/{sid}", headers=other).status_code == 404
    assert client.post(f"/api/chat/sessions/{sid}/messages", headers=other, json={"content": "x"}).status_code == 404
    assert client.get(f"/api/chat/sessions/{uuid.uuid4()}", headers=user["headers"]).status_code == 404
    assert client.get("/api/chat/sessions/not-a-uuid", headers=user["headers"]).status_code == 404

    assert client.patch(f"/api/chat/sessions/{sid}", headers=user["headers"], json={"title": "差旅"}).status_code == 200
    assert client.delete(f"/api/chat/sessions/{sid}", headers=user["headers"]).status_code == 200
    assert client.get(f"/api/chat/sessions/{sid}", headers=user["headers"]).status_code == 404


def test_memory_api_crud_and_isolation(client, user, make_user):
    created = client.post("/api/memories", headers=user["headers"],
                          json={"content": "关注毛利率而不是 GMV", "category": "preference"}).json()
    mid = created["memory"]["id"]
    patched = client.patch(f"/api/memories/{mid}", headers=user["headers"], json={"pinned": True}).json()
    assert patched["pinned"] is True
    other = make_user("viewer")
    assert client.delete(f"/api/memories/{mid}", headers=other).status_code == 404
    listing = client.get("/api/memories", headers=user["headers"]).json()
    assert listing["enabled"] is True and mid in [m["id"] for m in listing["items"]]
    assert client.delete(f"/api/memories/{mid}", headers=user["headers"]).status_code == 200
    assert client.delete("/api/memories", headers=user["headers"]).json()["deleted"] >= 0
