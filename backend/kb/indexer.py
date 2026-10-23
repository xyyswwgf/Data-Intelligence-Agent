"""
索引器 — 页面切块 + 向量化，写入 kb_chunks

- 页面创建/更新后异步索引（线程池），接口立即返回，前端显示索引状态
- Embedding 不可用时仍写入切块（关键词检索可用），状态标记为 partial，恢复后可一键补齐向量
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from backend.config import KB_INDEX_ASYNC
from backend.database import get_connection
from backend.kb import embeddings
from backend.kb.chunker import split_markdown

logger = logging.getLogger("nl2sql.kb.indexer")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kb-index")


def submit_index(page_id: int) -> None:
    if KB_INDEX_ASYNC:
        _executor.submit(_safe_index, page_id)
    else:
        _safe_index(page_id)


def _safe_index(page_id: int) -> None:
    try:
        index_page(page_id)
    except Exception as exc:  # 后台线程里的异常必须记录下来，否则会静默丢失
        logger.exception("页面 %s 索引失败", page_id)
        with get_connection() as conn:
            conn.execute(text("UPDATE wiki_pages SET index_status = 'failed', index_error = :e WHERE id = :id"),
                         {"e": str(exc)[:1000], "id": page_id})


def index_page(page_id: int) -> str:
    with get_connection() as conn:
        page = conn.execute(
            text("SELECT id, title, content_md, version FROM wiki_pages WHERE id = :id AND NOT is_deleted"),
            {"id": page_id},
        ).mappings().first()
        if not page:
            return "missing"
        conn.execute(text("UPDATE wiki_pages SET index_status = 'indexing' WHERE id = :id"), {"id": page_id})

    chunks = split_markdown(page["content_md"])
    vectors: list[list[float]] | None = None
    error = None
    if chunks and embeddings.enabled():
        try:
            # 向量化文本带上页面标题和小节路径，让短段落也有上下文
            vectors = embeddings.embed([
                f"{page['title']}\n{c.heading_path}\n{c.content}" for c in chunks
            ])
        except embeddings.EmbeddingError as exc:
            error = str(exc)
            logger.warning("页面 %s 向量化失败，降级为仅关键词检索: %s", page_id, exc)

    with get_connection() as conn:
        # 索引期间页面又被编辑：放弃本次结果，由新的索引任务负责
        latest = conn.execute(text("SELECT version FROM wiki_pages WHERE id = :id"), {"id": page_id}).scalar()
        if latest != page["version"]:
            return "stale"
        conn.execute(text("DELETE FROM kb_chunks WHERE page_id = :id"), {"id": page_id})
        for i, chunk in enumerate(chunks):
            conn.execute(
                text("""
                    INSERT INTO kb_chunks (page_id, chunk_index, heading_path, content, char_count, embedding, embedding_model)
                    VALUES (:pid, :i, :hp, :c, :n, CAST(:emb AS vector), :model)
                """),
                {"pid": page_id, "i": i, "hp": chunk.heading_path, "c": chunk.content, "n": len(chunk.content),
                 "emb": embeddings.to_pgvector(vectors[i]) if vectors else None,
                 "model": embeddings.model_name() if vectors else None},
            )
        status = "ready" if (vectors or not chunks or not embeddings.enabled()) else "partial"
        conn.execute(
            text("UPDATE wiki_pages SET index_status = :s, index_error = :e WHERE id = :id"),
            {"s": status, "e": error, "id": page_id},
        )
    return status


def pending_page_ids(include_partial: bool = True) -> list[int]:
    statuses = ("pending", "indexing", "failed", "partial") if include_partial else ("pending", "indexing", "failed")
    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT id FROM wiki_pages WHERE NOT is_deleted AND index_status = ANY(:s) ORDER BY id"),
            {"s": list(statuses)},
        ).all()
    return [r[0] for r in rows]


def reindex(all_pages: bool = False) -> int:
    """重建索引：默认只处理未完成/降级的页面；all_pages=True 时全量重建（例如更换 Embedding 模型后）。"""
    if all_pages:
        with get_connection() as conn:
            ids = [r[0] for r in conn.execute(text("SELECT id FROM wiki_pages WHERE NOT is_deleted ORDER BY id")).all()]
    else:
        ids = pending_page_ids()
    for page_id in ids:
        submit_index(page_id)
    return len(ids)
