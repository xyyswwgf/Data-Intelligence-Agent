"""
长期记忆 — 借鉴 Letta（分层记忆 + 核心记忆常驻）与 Mem0（自动抽取 + 去重合并）

  核心记忆（pinned）   每轮都放进上下文，如「我负责华北区域」「报告用表格呈现」
  召回记忆             按当前问题语义检索 Top-K
  写入路径             ① 用户明确说「记住…」② Agent 调用 remember 工具 ③ 每轮结束后由 LLM 自动抽取
  去重合并             与已有记忆相似度很高时更新原记录，而不是越存越多
  用户可控             可查看、编辑、置顶、删除，也可整体关闭记忆
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import text

from backend.config import MEMORY_ENABLED, MEMORY_RECALL_K
from backend.database import get_connection
from backend.kb import embeddings

logger = logging.getLogger("nl2sql.memory")

CATEGORIES = ("preference", "fact", "goal", "instruction")
CATEGORY_LABEL = {"preference": "偏好", "fact": "事实", "goal": "目标", "instruction": "指令"}
_DUP_VECTOR_SIM = 0.88
_DUP_TRGM_SIM = 0.6
_MAX_MEMORIES_PER_USER = 500
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory")

_EXPLICIT = re.compile(r"^\s*(?:请)?(?:帮我)?(?:记住|记一下|记下|以后)(?:[:：,，\s]*)(.+)$", re.S)


def memory_enabled_for(user_id: int) -> bool:
    if not MEMORY_ENABLED:
        return False
    with get_connection() as conn:
        value = conn.execute(text("SELECT memory_enabled FROM users WHERE id = :id"), {"id": user_id}).scalar()
    return bool(value)


def set_memory_enabled(user_id: int, enabled: bool) -> None:
    with get_connection() as conn:
        conn.execute(text("UPDATE users SET memory_enabled = :e WHERE id = :id"), {"e": enabled, "id": user_id})


def _embed(content: str) -> str | None:
    if not embeddings.available():
        return None
    try:
        return embeddings.to_pgvector(embeddings.embed([content])[0])
    except embeddings.EmbeddingError:
        return None


def _find_duplicate(conn, user_id: int, content: str, vec: str | None) -> int | None:
    if vec:
        row = conn.execute(text("""
            SELECT id, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM user_memories
            WHERE user_id = :u AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1
        """), {"u": user_id, "v": vec}).first()
        if row and row[1] >= _DUP_VECTOR_SIM:
            return row[0]
    row = conn.execute(text("""
        SELECT id FROM user_memories WHERE user_id = :u AND similarity(content, :c) >= :s
        ORDER BY similarity(content, :c) DESC LIMIT 1
    """), {"u": user_id, "c": content, "s": _DUP_TRGM_SIM}).first()
    return row[0] if row else None


def save_memory(
    user_id: int,
    content: str,
    category: str = "fact",
    source: str = "auto",
    session_id: str | None = None,
    pinned: bool = False,
) -> dict[str, Any]:
    """写入记忆；与已有记忆高度相似时合并（以新内容为准），返回 {action, memory}。"""
    content = re.sub(r"\s+", " ", content or "").strip()[:500]
    if len(content) < 2:
        raise ValueError("记忆内容太短")
    if category not in CATEGORIES:
        category = "fact"
    vec = _embed(content)
    with get_connection() as conn:
        dup_id = _find_duplicate(conn, user_id, content, vec)
        if dup_id:
            row = conn.execute(text("""
                UPDATE user_memories SET content = :c, category = :cat, embedding = COALESCE(CAST(:v AS vector), embedding),
                       pinned = pinned OR :p, updated_at = now()
                WHERE id = :id RETURNING *
            """), {"c": content, "cat": category, "v": vec, "p": pinned, "id": dup_id}).mappings().one()
            return {"action": "updated", "memory": _public(row)}
        count = conn.execute(text("SELECT COUNT(*) FROM user_memories WHERE user_id = :u"), {"u": user_id}).scalar()
        if count >= _MAX_MEMORIES_PER_USER:
            # 超出上限时淘汰最久未使用的非置顶记忆
            conn.execute(text("""
                DELETE FROM user_memories WHERE id = (
                    SELECT id FROM user_memories WHERE user_id = :u AND NOT pinned
                    ORDER BY COALESCE(last_used_at, created_at) ASC LIMIT 1)
            """), {"u": user_id})
        row = conn.execute(text("""
            INSERT INTO user_memories (user_id, content, category, pinned, source, source_session_id, embedding)
            VALUES (:u, :c, :cat, :p, :src, CAST(:sid AS uuid), CAST(:v AS vector))
            RETURNING *
        """), {"u": user_id, "c": content, "cat": category, "p": pinned, "src": source,
               "sid": session_id, "v": vec}).mappings().one()
    return {"action": "created", "memory": _public(row)}


def _public(row) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "content", "category", "pinned", "source", "use_count",
                                "last_used_at", "created_at", "updated_at")} | {
        "source_session_id": str(row["source_session_id"]) if row["source_session_id"] else None
    }


def list_memories(user_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT * FROM user_memories WHERE user_id = :u ORDER BY pinned DESC, updated_at DESC
        """), {"u": user_id}).mappings().all()
    return [_public(r) for r in rows]


def update_memory(user_id: int, memory_id: int, content: str | None = None,
                  category: str | None = None, pinned: bool | None = None) -> dict[str, Any]:
    sets, params = ["updated_at = now()"], {"id": memory_id, "u": user_id}
    if content is not None:
        content = content.strip()[:500]
        if len(content) < 2:
            raise ValueError("记忆内容太短")
        sets += ["content = :c", "embedding = COALESCE(CAST(:v AS vector), embedding)"]
        params |= {"c": content, "v": _embed(content)}
    if category is not None:
        if category not in CATEGORIES:
            raise ValueError("无效的记忆类型")
        sets.append("category = :cat")
        params["cat"] = category
    if pinned is not None:
        sets.append("pinned = :p")
        params["p"] = pinned
    with get_connection() as conn:
        row = conn.execute(text(f"UPDATE user_memories SET {', '.join(sets)} WHERE id = :id AND user_id = :u RETURNING *"),
                           params).mappings().first()
    if not row:
        raise LookupError("记忆不存在")
    return _public(row)


def delete_memory(user_id: int, memory_id: int) -> None:
    with get_connection() as conn:
        deleted = conn.execute(text("DELETE FROM user_memories WHERE id = :id AND user_id = :u"),
                               {"id": memory_id, "u": user_id}).rowcount
    if not deleted:
        raise LookupError("记忆不存在")


def clear_memories(user_id: int) -> int:
    with get_connection() as conn:
        return conn.execute(text("DELETE FROM user_memories WHERE user_id = :u"), {"u": user_id}).rowcount


def recall(user_id: int, query: str, k: int = MEMORY_RECALL_K) -> list[dict[str, Any]]:
    """核心记忆（置顶）全部返回 + 与问题最相关的 k 条；记录使用次数，便于淘汰冷记忆。"""
    with get_connection() as conn:
        pinned = conn.execute(text("""
            SELECT id, content, category, TRUE AS pinned FROM user_memories
            WHERE user_id = :u AND pinned ORDER BY updated_at DESC LIMIT 10
        """), {"u": user_id}).mappings().all()
        vec = _embed(query) if query else None
        if vec:
            related = conn.execute(text("""
                SELECT id, content, category, FALSE AS pinned FROM user_memories
                WHERE user_id = :u AND NOT pinned AND embedding IS NOT NULL
                  AND 1 - (embedding <=> CAST(:v AS vector)) >= 0.45
                ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k
            """), {"u": user_id, "v": vec, "k": k}).mappings().all()
        else:
            related = conn.execute(text("""
                SELECT id, content, category, FALSE AS pinned FROM user_memories
                WHERE user_id = :u AND NOT pinned
                ORDER BY word_similarity(:q, content) DESC, updated_at DESC LIMIT :k
            """), {"u": user_id, "q": query or "", "k": k}).mappings().all()
        rows = [dict(r) for r in pinned] + [dict(r) for r in related]
        if rows:
            conn.execute(text("""
                UPDATE user_memories SET use_count = use_count + 1, last_used_at = now() WHERE id = ANY(:ids)
            """), {"ids": [r["id"] for r in rows]})
    return rows


def explicit_memory_request(question: str) -> str | None:
    """识别「记住：我负责华北区域」这类明确的记忆指令（不依赖大模型）。"""
    match = _EXPLICIT.match(question)
    if not match:
        return None
    content = match.group(1).strip().rstrip("。.!！")
    return content if len(content) >= 2 else None


_EXTRACT_SYSTEM = """你是记忆提炼器。从一轮对话中提炼「关于这位用户、长期有用」的信息，供以后的对话个性化使用。
只提炼这几类：
- preference 偏好：表达方式、图表/报告格式、关注的指标口径等
- fact 事实：用户的部门、负责的区域/业务线、所用系统等
- goal 目标：持续在推进的目标或项目
- instruction 指令：用户要求以后一直遵守的做法
不要提炼：一次性的数据查询结果、数字、寒暄、模型自己的结论。
输出严格的 JSON 数组，例如 [{"content": "用户负责华北区域的销售分析", "category": "fact"}]；没有可提炼的内容输出 []。"""


def extract_from_turn(user_id: int, session_id: str, question: str, answer: str, provider=None) -> list[dict]:
    """Mem0 式自动抽取：由 LLM 判断本轮是否有值得长期记住的信息。"""
    from backend.llm_gateway import LLMError, get_provider

    provider = provider or get_provider()
    if not provider.configured():
        return []
    try:
        raw = provider.complete(
            _EXTRACT_SYSTEM,
            f"用户：{question[:1500]}\n\n助手：{answer[:1500]}",
            max_tokens=800,
            temperature=0,
        )
    except LLMError as exc:
        logger.warning("记忆抽取失败: %s", exc)
        return []
    match = re.search(r"\[[\s\S]*\]", raw)
    try:
        items = json.loads(match.group(0)) if match else []
    except json.JSONDecodeError:
        return []
    saved = []
    for item in items[:5]:
        if isinstance(item, dict) and item.get("content"):
            try:
                saved.append(save_memory(user_id, str(item["content"]), str(item.get("category", "fact")),
                                         "auto", session_id))
            except ValueError:
                continue
    return saved


def extract_in_background(user_id: int, session_id: str, question: str, answer: str, provider=None) -> None:
    def job():
        try:
            extract_from_turn(user_id, session_id, question, answer, provider)
        except Exception:
            logger.exception("后台记忆抽取失败")

    _executor.submit(job)
