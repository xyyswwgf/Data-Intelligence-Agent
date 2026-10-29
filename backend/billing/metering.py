"""
计量：把一次请求内的所有大模型调用记到发起请求的用户名下

对话接口用 metered_events 包住事件流，期间 llm_gateway 每次 chat 调用结束都会调用 record()。
没有计量上下文的调用（管理员测试连接、后台任务）不计费。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

logger = logging.getLogger("nl2sql.billing")


@dataclass(frozen=True)
class BillingContext:
    user_id: int
    session_id: str | None = None
    mode: str | None = None


_current: ContextVar[BillingContext | None] = ContextVar("billing_context", default=None)


def metered_events(events: Iterable[dict], ctx: BillingContext) -> Iterator[dict]:
    """
    逐步执行事件生成器，每一步都在计量上下文中运行。
    流式响应的每一步可能在不同线程里执行，所以每次 next() 前重新设置上下文。
    """
    it = iter(events)
    while True:
        token = _current.set(ctx)
        try:
            event = next(it)
        except StopIteration:
            return
        finally:
            _current.reset(token)
        yield event


def record(provider: Any, response: Any) -> None:
    """llm_gateway 在每次 chat 调用后调用。计费失败只记日志，不影响回答。"""
    ctx = _current.get()
    if ctx is None:
        return
    input_tokens = int(getattr(response, "input_tokens", 0) or 0)
    output_tokens = int(getattr(response, "output_tokens", 0) or 0)
    if not input_tokens and not output_tokens:
        return
    from backend.billing.accounts import charge

    try:
        charge(ctx.user_id, ctx.session_id, ctx.mode, getattr(provider, "profile_id", None),
               getattr(provider, "model", "") or "", input_tokens, output_tokens)
    except Exception:
        logger.exception("记录使用量失败")
