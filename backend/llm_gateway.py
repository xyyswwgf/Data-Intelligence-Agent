"""
LLM 网关 — 屏蔽不同大模型 API 协议的差异，供 NL2SQL、RAG、推理 Agent 统一调用

支持两种协议（覆盖国内外主流 API 与自部署推理服务）：
  - anthropic          DeepSeek / Claude 的 Anthropic 兼容接口
  - openai_compatible  DeepSeek / 通义千问 / 智谱 / vLLM / SGLang / Ollama 等 OpenAI 兼容接口

统一的消息格式（内部）：
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [ToolCall...], "provider_blocks": [...]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

原生推理（thinking / reasoning）在两种协议下都被解析成 LLMResponse.reasoning，
上层的推理循环不关心模型来自哪家。
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from backend.config import (
    ANTHROPIC_BASE_URL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_CONTEXT_WINDOW,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MODEL,
    LLM_NO_REASONING_EFFORT,
    LLM_PROVIDER,
    LLM_REASONING_EFFORT,
    LLM_THINKING_BUDGET,
    LLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger("nl2sql.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    # 原样保留的协议块（Anthropic 开启 thinking + 工具调用时，下一轮必须回传 thinking 块）
    provider_blocks: list[dict] | None = None


@dataclass
class ModelSettings:
    """单个模型配置的运行参数（来自环境变量或「模型管理」中的配置）。"""

    context_window: int = LLM_CONTEXT_WINDOW
    max_output_tokens: int = LLM_MAX_OUTPUT_TOKENS
    thinking_budget: int = LLM_THINKING_BUDGET
    reasoning_effort: str = LLM_REASONING_EFFORT
    no_reasoning_effort: str = LLM_NO_REASONING_EFFORT
    timeout_seconds: float = LLM_TIMEOUT_SECONDS


def _metered(chat):
    """包装 Provider 子类的 chat：调用结束后把 token 用量交给计费模块（没有计量上下文时什么都不做）。"""

    @functools.wraps(chat)
    def wrapper(self, *args, **kwargs):
        response = chat(self, *args, **kwargs)
        from backend.billing.metering import record

        record(self, response)
        return response

    wrapper.__metered__ = True
    return wrapper


def _metered_stream(stream_chat):
    """包装流式调用：生成器结束时拿到完整的 LLMResponse，再交给计费模块。"""

    @functools.wraps(stream_chat)
    def wrapper(self, *args, **kwargs):
        response = yield from stream_chat(self, *args, **kwargs)
        from backend.billing.metering import record

        record(self, response)
        return response

    wrapper.__metered__ = True
    return wrapper


class Provider:
    name = "base"
    model = ""
    profile_id: int | None = None
    profile_name = "环境变量配置"
    kind = "cloud"
    settings = ModelSettings()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        chat = cls.__dict__.get("chat")
        if chat is not None and not getattr(chat, "__metered__", False):
            cls.chat = _metered(chat)
        stream = cls.__dict__.get("stream_chat")
        if stream is not None and not getattr(stream, "__metered__", False):
            cls.stream_chat = _metered_stream(stream)

    def configured(self) -> bool:
        raise NotImplementedError

    def chat(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        thinking: bool = False,
    ) -> LLMResponse:
        raise NotImplementedError

    def stream_chat(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        thinking: bool = False,
    ) -> Iterator[tuple[str, str]]:
        """
        流式调用：逐段产出 ("text" | "reasoning", 增量文本)，生成器的返回值是完整的 LLMResponse
        （用法：resp = yield from provider.stream_chat(...)）。
        不支持流式的 Provider 退化为一次 chat，整段产出。
        """
        resp = self.chat(system, messages, tools=tools, max_tokens=max_tokens, temperature=temperature, thinking=thinking)
        if resp.reasoning:
            yield "reasoning", resp.reasoning
        if resp.text:
            yield "text", resp.text
        return resp

    def complete(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2) -> str:
        """单轮文本补全的便捷方法。"""
        resp = self.chat(system, [{"role": "user", "content": user}], max_tokens=max_tokens, temperature=temperature)
        if not resp.text.strip():
            hint = "（推理模型的思考过程耗尽了输出额度，可设置 LLM_NO_REASONING_EFFORT=none 关闭后台任务的推理）" if resp.reasoning else ""
            raise LLMError(f"大模型返回了空内容{hint}")
        return resp.text.strip()


# ---------------------------------------------------------------------------
# Anthropic 协议
# ---------------------------------------------------------------------------


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str, model: str, settings: ModelSettings | None = None):
        self.api_key, self.base_url, self.model = api_key, base_url, model
        self.settings = settings or ModelSettings()
        self._client = None

    def configured(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=self.api_key, base_url=self.base_url, timeout=self.settings.timeout_seconds, max_retries=2
            )
        return self._client

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for msg in messages:
            if msg["role"] == "user":
                out.append({"role": "user", "content": msg["content"]})
            elif msg["role"] == "assistant":
                if msg.get("provider_blocks"):
                    out.append({"role": "assistant", "content": msg["provider_blocks"]})
                    continue
                blocks: list[dict] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for call in msg.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
                out.append({"role": "assistant", "content": blocks or msg.get("content", "")})
            elif msg["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"]}
                # Anthropic 要求同一轮的多个工具结果放在同一条 user 消息里
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def _kwargs(self, system, messages, tools, max_tokens, temperature, thinking) -> dict[str, Any]:
        if not self.configured():
            raise LLMError("未配置大模型 API Key")
        max_tokens = max_tokens or self.settings.max_output_tokens
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": self._convert(messages),
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        if thinking and self.settings.thinking_budget > 0:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.settings.thinking_budget}
            kwargs["max_tokens"] = max_tokens + self.settings.thinking_budget
        else:
            kwargs["thinking"] = {"type": "disabled"}
            kwargs["temperature"] = temperature
        return kwargs

    @staticmethod
    def _parse(response) -> LLMResponse:
        result = LLMResponse(stop_reason=response.stop_reason or "")
        blocks = []
        for block in response.content:
            btype = getattr(block, "type", "")
            dumped = block.model_dump() if hasattr(block, "model_dump") else None
            if btype == "text":
                result.text += block.text
            elif btype == "thinking":
                result.reasoning += getattr(block, "thinking", "")
            elif btype == "tool_use":
                result.tool_calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
            if dumped:
                blocks.append(dumped)
        usage = getattr(response, "usage", None)
        result.input_tokens = getattr(usage, "input_tokens", 0) or 0
        result.output_tokens = getattr(usage, "output_tokens", 0) or 0
        if result.reasoning and result.tool_calls:
            result.provider_blocks = blocks
        return result

    def chat(self, system, messages, tools=None, max_tokens=None, temperature=0.2, thinking=False):
        kwargs = self._kwargs(system, messages, tools, max_tokens, temperature, thinking)
        try:
            response = self._get_client().messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"大模型 API 调用失败: {exc}") from exc
        return self._parse(response)

    def stream_chat(self, system, messages, tools=None, max_tokens=None, temperature=0.2, thinking=False):
        kwargs = self._kwargs(system, messages, tools, max_tokens, temperature, thinking)
        try:
            with self._get_client().messages.stream(**kwargs) as stream:
                for event in stream:
                    if getattr(event, "type", "") != "content_block_delta":
                        continue
                    delta = event.delta
                    if delta.type == "text_delta" and delta.text:
                        yield "text", delta.text
                    elif delta.type == "thinking_delta" and getattr(delta, "thinking", ""):
                        yield "reasoning", delta.thinking
                final = stream.get_final_message()
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"大模型 API 调用失败: {exc}") from exc
        return self._parse(final)


# ---------------------------------------------------------------------------
# OpenAI 兼容协议
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"

    def __init__(self, api_key: str, base_url: str, model: str, settings: ModelSettings | None = None):
        self.api_key, self.base_url, self.model = api_key, base_url, model
        self.settings = settings or ModelSettings()

    def configured(self) -> bool:
        # 本地推理服务（Ollama / vLLM）通常不需要 Key，只要配置了地址即可
        return bool(self.base_url)

    @staticmethod
    def _convert(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}] if system else []
        for msg in messages:
            if msg["role"] == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
                if msg.get("tool_calls"):
                    item["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                        for c in msg["tool_calls"]
                    ]
                out.append(item)
            elif msg["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": msg["tool_call_id"], "content": msg["content"]})
            else:
                out.append({"role": msg["role"], "content": msg["content"]})
        return out

    def _body(self, system, messages, tools, max_tokens, temperature, thinking) -> dict[str, Any]:
        if not self.configured():
            raise LLMError("未配置大模型接口地址 LLM_BASE_URL")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert(system, messages),
            "max_tokens": max_tokens or self.settings.max_output_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        if thinking and self.settings.reasoning_effort:
            body["reasoning_effort"] = self.settings.reasoning_effort
        elif not thinking and self.settings.no_reasoning_effort:
            body["reasoning_effort"] = self.settings.no_reasoning_effort
        return body

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _tool_calls(raw_calls: list[dict]) -> list[ToolCall]:
        calls = []
        for i, call in enumerate(raw_calls):
            fn = call.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            calls.append(ToolCall(call.get("id") or f"call_{i}", fn.get("name", ""), args))
        return calls

    def chat(self, system, messages, tools=None, max_tokens=None, temperature=0.2, thinking=False):
        body = self._body(system, messages, tools, max_tokens, temperature, thinking)
        try:
            with httpx.Client(timeout=self.settings.timeout_seconds) as client:
                resp = client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        except Exception as exc:
            raise LLMError(f"大模型 API 调用失败: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"大模型 API 返回 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        result = LLMResponse(
            text=message.get("content") or "",
            # DeepSeek 用 reasoning_content，Ollama / 部分 vLLM 用 reasoning
            reasoning=message.get("reasoning_content") or message.get("reasoning") or "",
            stop_reason=choice.get("finish_reason") or "",
            tool_calls=self._tool_calls(message.get("tool_calls") or []),
        )
        usage = data.get("usage") or {}
        result.input_tokens = usage.get("prompt_tokens", 0) or 0
        result.output_tokens = usage.get("completion_tokens", 0) or 0
        return result

    def stream_chat(self, system, messages, tools=None, max_tokens=None, temperature=0.2, thinking=False):
        body = self._body(system, messages, tools, max_tokens, temperature, thinking)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}  # 最后一个分片带 token 用量（计费用）
        result = LLMResponse()
        calls: dict[int, dict] = {}  # 工具调用按 index 分片到达，参数是逐段拼接的字符串
        try:
            with httpx.Client(timeout=self.settings.timeout_seconds) as client:
                resp = client.send(client.build_request("POST", f"{self.base_url}/chat/completions",
                                                        headers=self._headers(), json=body), stream=True)
                if resp.status_code == 400 and "stream_options" in body:
                    # 部分推理服务不认识 stream_options：去掉后重试（这时拿不到用量，按 0 计）
                    resp.close()
                    body.pop("stream_options")
                    resp = client.send(client.build_request("POST", f"{self.base_url}/chat/completions",
                                                            headers=self._headers(), json=body), stream=True)
                try:
                    if resp.status_code >= 400:
                        raise LLMError(f"大模型 API 返回 {resp.status_code}: {resp.read().decode(errors='replace')[:300]}")
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        chunk = json.loads(payload)
                        if chunk.get("usage"):
                            result.input_tokens = chunk["usage"].get("prompt_tokens", 0) or 0
                            result.output_tokens = chunk["usage"].get("completion_tokens", 0) or 0
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if reasoning:
                                result.reasoning += reasoning
                                yield "reasoning", reasoning
                            if delta.get("content"):
                                result.text += delta["content"]
                                yield "text", delta["content"]
                            for part in delta.get("tool_calls") or []:
                                slot = calls.setdefault(part.get("index", len(calls)), {"id": None, "function": {"name": "", "arguments": ""}})
                                slot["id"] = part.get("id") or slot["id"]
                                fn = part.get("function") or {}
                                slot["function"]["name"] += fn.get("name") or ""
                                slot["function"]["arguments"] += fn.get("arguments") or ""
                            if choice.get("finish_reason"):
                                result.stop_reason = choice["finish_reason"]
                finally:
                    resp.close()
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"大模型 API 调用失败: {exc}") from exc
        result.tool_calls = self._tool_calls([calls[k] for k in sorted(calls)])
        return result


# ---------------------------------------------------------------------------
# 工厂：模型配置（数据库）优先，未配置时回退到环境变量
# ---------------------------------------------------------------------------

_env_provider: Provider | None = None
_override: Provider | None = None
_cache: dict[tuple, Provider] = {}


def build_provider(provider: str, base_url: str, api_key: str, model: str, settings: ModelSettings) -> Provider:
    if provider == "openai_compatible":
        return OpenAICompatibleProvider(api_key, base_url.rstrip("/"), model, settings)
    return AnthropicProvider(api_key, base_url.rstrip("/"), model, settings)


def _env() -> Provider:
    global _env_provider
    if _env_provider is None:
        base = LLM_BASE_URL if LLM_PROVIDER == "openai_compatible" else ANTHROPIC_BASE_URL
        _env_provider = build_provider(LLM_PROVIDER, base, LLM_API_KEY, LLM_MODEL, ModelSettings())
    return _env_provider


def _from_profile(row: dict) -> Provider:
    key = (row["id"], str(row["updated_at"]))
    cached = _cache.get(key)
    if cached:
        return cached
    from backend.secrets_store import decrypt

    settings = ModelSettings(
        context_window=row["context_window"], max_output_tokens=row["max_output_tokens"],
        thinking_budget=row["thinking_budget"], reasoning_effort=row["reasoning_effort"],
        no_reasoning_effort=row["no_reasoning_effort"], timeout_seconds=row["timeout_seconds"],
    )
    p = build_provider(row["provider"], row["base_url"], decrypt(row["api_key_encrypted"]), row["model"], settings)
    p.profile_id, p.profile_name, p.kind = row["id"], row["name"], row["kind"]
    _cache.clear()  # 配置更新后旧实例失效
    _cache[key] = p
    return p


def get_provider(profile_id: int | None = None) -> Provider:
    """
    选择模型：显式指定的配置 → 数据库中的默认配置 → 环境变量。
    指定的配置不存在或已停用时回退到默认，保证对话不中断。
    """
    if _override is not None:
        return _override
    try:
        from backend.database import execute_sql

        rows = execute_sql(
            """
            SELECT * FROM llm_profiles WHERE enabled
              AND (id = CAST(:id AS BIGINT) OR is_default)
            ORDER BY (id = CAST(:id AS BIGINT)) DESC NULLS LAST, is_default DESC LIMIT 1
            """,
            {"id": profile_id},
        )
        if rows["data"]:
            return _from_profile(dict(zip(rows["columns"], rows["data"][0])))
    except Exception as exc:  # 表还未迁移等情况
        logger.debug("读取模型配置失败，使用环境变量: %s", exc)
    return _env()


def set_provider(provider: Provider | None) -> None:
    """测试用：强制所有调用使用指定 Provider；传 None 恢复正常选择逻辑。"""
    global _override
    _override = provider


def llm_configured(profile_id: int | None = None) -> bool:
    return get_provider(profile_id).configured()


def describe(provider: Provider | None = None) -> dict:
    p = provider or get_provider()
    return {"provider": p.name, "model": getattr(p, "model", ""), "configured": p.configured(),
            "profile_id": p.profile_id, "profile_name": p.profile_name, "kind": p.kind}
