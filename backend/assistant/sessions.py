"""
对话会话 — 消息持久化、滚动摘要压缩

借鉴长对话产品的做法：最近若干轮原样保留；更早的消息在超过预算时压缩成「滚动摘要」，
摘要记录用户目标、已确认的结论和关键数字、未完成事项，既控制上下文长度又不丢关键信息。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from backend.assistant.tokens import estimate_tokens
from backend.config import CONTEXT_COMPACT_RATIO, CONTEXT_KEEP_RECENT, LLM_CONTEXT_WINDOW
from backend.database import get_connection

logger = logging.getLogger("nl2sql.sessions")


def set_session_model(session_id: str, profile_id: int | None) -> None:
    with get_connection() as conn:
        conn.execute(text("UPDATE chat_sessions SET model_profile_id = :p WHERE id = CAST(:s AS uuid)"),
                     {"p": profile_id, "s": session_id})


def create_session(user_id: int, title: str | None = None) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(text("""
            INSERT INTO chat_sessions (user_id, title) VALUES (:u, :t)
            RETURNING id::text AS id, title, summary, message_count, created_at, updated_at
        """), {"u": user_id, "t": (title or "新对话")[:200]}).mappings().one()
    return dict(row)


def list_sessions(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT id::text AS id, title, message_count, created_at, updated_at, summary <> '' AS compacted
            FROM chat_sessions WHERE user_id = :u ORDER BY updated_at DESC LIMIT :l
        """), {"u": user_id, "l": limit}).mappings().all()
    return [dict(r) for r in rows]


def get_session(user_id: int, session_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(text("""
            SELECT id::text AS id, user_id, title, summary, summary_upto, message_count, created_at, updated_at
            FROM chat_sessions WHERE id = CAST(:s AS uuid) AND user_id = :u
        """), {"s": session_id, "u": user_id}).mappings().first()
    return dict(row) if row else None


def get_messages(session_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT id, seq, role, mode, content, payload, trace, token_count, created_at
            FROM chat_messages WHERE session_id = CAST(:s AS uuid) AND seq > :a ORDER BY seq
        """), {"s": session_id, "a": after_seq}).mappings().all()
    return [dict(r) for r in rows]


def rename_session(user_id: int, session_id: str, title: str) -> None:
    with get_connection() as conn:
        updated = conn.execute(text("""
            UPDATE chat_sessions SET title = :t WHERE id = CAST(:s AS uuid) AND user_id = :u
        """), {"t": title.strip()[:200] or "新对话", "s": session_id, "u": user_id}).rowcount
    if not updated:
        raise LookupError("会话不存在")


def delete_session(user_id: int, session_id: str) -> None:
    with get_connection() as conn:
        deleted = conn.execute(text("DELETE FROM chat_sessions WHERE id = CAST(:s AS uuid) AND user_id = :u"),
                               {"s": session_id, "u": user_id}).rowcount
    if not deleted:
        raise LookupError("会话不存在")


def add_message(session_id: str, role: str, content: str, mode: str = "assistant",
                payload: dict | None = None, trace: list | dict | None = None) -> dict[str, Any]:
    with get_connection() as conn:
        # 锁住会话行，保证同一会话并发写入时序号不冲突
        conn.execute(text("SELECT 1 FROM chat_sessions WHERE id = CAST(:s AS uuid) FOR UPDATE"), {"s": session_id})
        seq = conn.execute(text("SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_messages WHERE session_id = CAST(:s AS uuid)"),
                           {"s": session_id}).scalar()
        row = conn.execute(text("""
            INSERT INTO chat_messages (session_id, seq, role, mode, content, payload, trace, token_count)
            VALUES (CAST(:s AS uuid), :seq, :role, :mode, :content, CAST(:payload AS jsonb), CAST(:trace AS jsonb), :tokens)
            RETURNING id, seq, role, mode, content, created_at
        """), {
            "s": session_id, "seq": seq, "role": role, "mode": mode, "content": content,
            "payload": json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
            "trace": json.dumps(trace, ensure_ascii=False, default=str) if trace is not None else None,
            "tokens": estimate_tokens(content),
        }).mappings().one()
        conn.execute(text("""
            UPDATE chat_sessions SET message_count = message_count + 1, updated_at = now() WHERE id = CAST(:s AS uuid)
        """), {"s": session_id})
    return dict(row)


def auto_title(session_id: str, first_question: str) -> str:
    title = first_question.strip().replace("\n", " ")
    title = title[:28] + ("…" if len(title) > 28 else "")
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE chat_sessions SET title = :t WHERE id = CAST(:s AS uuid) AND title = '新对话'
        """), {"t": title or "新对话", "s": session_id})
    return title


_SUMMARY_SYSTEM = """你负责维护一段对话的「滚动摘要」，供后续对话作为上下文。
把【已有摘要】和【新增对话】合并成新的摘要，要求：
- 保留：用户的目标与关注点、已确认的结论、关键数字与口径、做过的分析/查询、未完成事项
- 删除：寒暄、重复内容、推理过程细节
- 用中文要点列表，控制在 400 字以内"""


def maybe_compact(session_id: str, provider=None) -> bool:
    """未压缩消息超过预算时，把较早的消息合并进滚动摘要，只保留最近 CONTEXT_KEEP_RECENT 条原文。"""
    with get_connection() as conn:
        session = conn.execute(text("SELECT summary, summary_upto FROM chat_sessions WHERE id = CAST(:s AS uuid)"),
                               {"s": session_id}).mappings().first()
    if not session:
        return False
    messages = get_messages(session_id, session["summary_upto"])
    total = sum(m["token_count"] for m in messages)
    from backend.llm_gateway import LLMError, get_provider

    provider = provider or get_provider()
    window = provider.settings.context_window or LLM_CONTEXT_WINDOW
    if total <= window * CONTEXT_COMPACT_RATIO or len(messages) <= CONTEXT_KEEP_RECENT:
        return False
    to_fold = messages[:-CONTEXT_KEEP_RECENT]
    transcript = "\n".join(f"{'用户' if m['role'] == 'user' else '助手'}：{m['content'][:1500]}" for m in to_fold)

    summary = None
    if provider.configured():
        try:
            summary = provider.complete(
                _SUMMARY_SYSTEM, f"【已有摘要】\n{session['summary'] or '（无）'}\n\n【新增对话】\n{transcript}",
                max_tokens=800, temperature=0,
            )
        except LLMError as exc:
            logger.warning("会话摘要生成失败，降级为截断摘要: %s", exc)
    if not summary:
        # 无模型时的降级：保留每条消息的开头，至少不丢话题线索
        lines = [f"- {'用户' if m['role'] == 'user' else '助手'}：{m['content'][:80]}" for m in to_fold]
        summary = "\n".join(filter(None, [session["summary"], *lines]))[-3000:]
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE chat_sessions SET summary = :sum, summary_upto = :upto WHERE id = CAST(:s AS uuid)
        """), {"sum": summary, "upto": to_fold[-1]["seq"], "s": session_id})
    logger.info("会话 %s 已压缩 %d 条消息进摘要", session_id, len(to_fold))
    return True
