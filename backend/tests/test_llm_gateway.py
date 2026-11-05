"""LLM 网关的协议细节（不联网）：最后一轮的 tool_choice、thinking 块回传、配置缓存。"""

from types import SimpleNamespace

from backend import llm_gateway
from backend.llm_gateway import (AnthropicProvider, LLMResponse, ModelSettings, OpenAICompatibleProvider, Provider,
                                 ToolCall, ToolSpec)

SPEC = [ToolSpec("run_sql", "执行 SQL", {"type": "object", "properties": {}})]
HISTORY = [
    {"role": "user", "content": "问题"},
    {"role": "assistant", "content": "", "tool_calls": [ToolCall("t1", "run_sql", {"sql": "SELECT 1"})]},
    {"role": "tool", "tool_call_id": "t1", "name": "run_sql", "content": "1"},
]


def test_anthropic_final_round_keeps_tools_with_tool_choice_none():
    p = AnthropicProvider("k", "https://example.invalid", "m", ModelSettings(thinking_budget=1024))
    kwargs = p._kwargs("sys", HISTORY, SPEC, None, 0.2, True, "none")
    assert kwargs["tools"] and kwargs["tool_choice"] == {"type": "none"}
    assert kwargs["thinking"]["type"] == "enabled" and "temperature" not in kwargs
    assert "tool_choice" not in p._kwargs("sys", HISTORY, SPEC, None, 0.2, False)


def test_openai_tool_choice():
    p = OpenAICompatibleProvider("", "http://localhost:8000/v1", "m")
    assert p._body("sys", HISTORY, SPEC, None, 0.2, False, "none")["tool_choice"] == "none"
    assert "tool_choice" not in p._body("sys", HISTORY, SPEC, None, 0.2, False)


class _Block(SimpleNamespace):
    def model_dump(self):
        return dict(vars(self))


def test_anthropic_keeps_redacted_thinking_and_strips_none():
    response = SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=5, output_tokens=7), content=[
        _Block(type="redacted_thinking", data="xyz"),
        _Block(type="text", text="先查一下", citations=None),
        _Block(type="tool_use", id="t1", name="run_sql", input={"sql": "SELECT 1"}),
    ])
    parsed = AnthropicProvider._parse(response)
    assert parsed.tool_calls[0].name == "run_sql"
    # 只有 redacted_thinking（没有可读的思考文本）也必须原样回传
    assert parsed.provider_blocks and parsed.provider_blocks[0]["type"] == "redacted_thinking"
    assert "citations" not in parsed.provider_blocks[1]


def test_base_stream_skips_tool_choice_for_old_providers():
    class Old(Provider):
        def configured(self):
            return True

        def chat(self, system, messages, tools=None, max_tokens=None, temperature=0.2, thinking=False):
            return LLMResponse(text="ok")

    gen = Old().stream_chat("s", [], tools=SPEC, tool_choice="none")
    assert list(gen) == [("text", "ok")]


def test_profile_cache_keeps_other_profiles(monkeypatch):
    monkeypatch.setattr("backend.secrets_store.decrypt", lambda v: "key")
    llm_gateway._cache.clear()
    row = dict(provider="openai_compatible", base_url="http://a/v1", api_key_encrypted="x", model="m", name="A",
               kind="local", context_window=8000, max_output_tokens=1000, thinking_budget=0, reasoning_effort="",
               no_reasoning_effort="", timeout_seconds=30)
    a = llm_gateway._from_profile({**row, "id": 1, "updated_at": "t1"})
    b = llm_gateway._from_profile({**row, "id": 2, "updated_at": "t1"})
    assert llm_gateway._from_profile({**row, "id": 1, "updated_at": "t1"}) is a  # 交替使用两个配置不互相清缓存
    a2 = llm_gateway._from_profile({**row, "id": 1, "updated_at": "t2"})
    assert a2 is not a and llm_gateway._from_profile({**row, "id": 2, "updated_at": "t1"}) is b
    llm_gateway._cache.clear()
