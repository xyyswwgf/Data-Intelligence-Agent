"""
上下文管理 — 按 token 预算、按优先级组装每一轮发给模型的上下文

  优先级（高 → 低）              预算策略
  ① 系统指令 + 工具定义          必须保留
  ② 当前问题                     必须保留（超长时截断）
  ③ 核心记忆（置顶）              ≤ 6%
  ④ 召回的相关记忆                ≤ 6%
  ⑤ 会话滚动摘要                  ≤ 12%
  ⑥ 最近的对话原文                剩余预算，从最新往前填，至少保留上一轮
超出预算时从低优先级开始裁剪，并把各部分占用返回给前端展示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.assistant import memory as memory_store
from backend.assistant.sessions import get_messages
from backend.assistant.tokens import estimate_tokens, truncate_to_tokens
from backend.config import LLM_CONTEXT_WINDOW, LLM_MAX_OUTPUT_TOKENS


@dataclass
class BuiltContext:
    system: str
    messages: list[dict]
    usage: dict[str, int] = field(default_factory=dict)
    memories: list[dict] = field(default_factory=list)
    dropped_messages: int = 0

    def total(self) -> int:
        return sum(self.usage.values())


def _memory_block(title: str, items: list[dict], budget: int) -> str:
    if not items:
        return ""
    lines = [f"- [{memory_store.CATEGORY_LABEL.get(m['category'], m['category'])}] {m['content']}" for m in items]
    block = f"## {title}\n" + "\n".join(lines)
    return truncate_to_tokens(block, budget)


def build_context(
    base_system: str,
    tools_schema: list[dict],
    session: dict,
    user_id: int,
    question: str,
    use_memory: bool,
    window: int = LLM_CONTEXT_WINDOW,
) -> BuiltContext:
    budget = window - LLM_MAX_OUTPUT_TOKENS - 512  # 预留输出与推理余量
    usage: dict[str, int] = {}

    usage["system"] = estimate_tokens(base_system)
    usage["tools"] = estimate_tokens(json.dumps(tools_schema, ensure_ascii=False))
    question = truncate_to_tokens(question, max(500, budget // 4))
    usage["question"] = estimate_tokens(question)
    remaining = budget - usage["system"] - usage["tools"] - usage["question"]

    sections: list[str] = []
    memories: list[dict] = []
    if use_memory:
        memories = memory_store.recall(user_id, question)
        pinned = [m for m in memories if m["pinned"]]
        related = [m for m in memories if not m["pinned"]]
        core = _memory_block("关于用户的核心记忆（长期有效）", pinned, int(budget * 0.06))
        recalled = _memory_block("可能相关的用户记忆", related, int(budget * 0.06))
        for key, block in (("core_memory", core), ("recalled_memory", recalled)):
            usage[key] = estimate_tokens(block)
            remaining -= usage[key]
            if block:
                sections.append(block)

    if session.get("summary"):
        summary = "## 本次对话的早期摘要\n" + truncate_to_tokens(session["summary"], int(budget * 0.12))
        usage["summary"] = estimate_tokens(summary)
        remaining -= usage["summary"]
        sections.append(summary)

    # 最近的对话原文：从最新往前填，直到预算用完；超长消息截断
    history = get_messages(session["id"], session.get("summary_upto", 0))
    picked: list[dict] = []
    used = 0
    for msg in reversed(history):
        content = msg["content"] or ""
        cost = estimate_tokens(content)
        if used + cost > remaining:
            if len(picked) < 2:  # 至少保留上一轮问答
                content = truncate_to_tokens(content, max(200, remaining - used))
                cost = estimate_tokens(content)
            else:
                break
        picked.append({"role": msg["role"], "content": content})
        used += cost
    picked.reverse()
    # Anthropic / OpenAI 都要求对话以 user 开头
    while picked and picked[0]["role"] != "user":
        picked.pop(0)
    usage["history"] = used
    dropped = len(history) - len(picked)

    system = base_system + ("\n\n" + "\n\n".join(sections) if sections else "")
    messages = picked + [{"role": "user", "content": question}]
    return BuiltContext(system=system, messages=messages, usage=usage, memories=memories, dropped_messages=dropped)


def usage_report(ctx: BuiltContext, window: int = LLM_CONTEXT_WINDOW) -> dict[str, Any]:
    return {"window": window, "used": ctx.total(), "parts": ctx.usage, "dropped_messages": ctx.dropped_messages,
            "memories": [{"id": m["id"], "content": m["content"], "pinned": m["pinned"]} for m in ctx.memories]}
