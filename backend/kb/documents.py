"""
文档入库 — 上传 → 落盘（sha256 去重）→ 转 Markdown → 建 Wiki 页面 → 异步索引
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from sqlalchemy import text

from backend.config import KB_MAX_FILE_MB, KB_STORAGE_DIR
from backend.database import get_connection
from backend.kb import wiki
from backend.kb.converter import convert_to_markdown, file_type_of

logger = logging.getLogger("nl2sql.kb.documents")


class DuplicateDocument(Exception):
    def __init__(self, document: dict):
        super().__init__("该文件已上传过")
        self.document = document


def _storage_path(sha256: str, file_type: str) -> str:
    return os.path.join(KB_STORAGE_DIR, sha256[:2], f"{sha256}.{file_type}")


def ingest(filename: str, data: bytes, user_id: int | None, parent_id: int | None = None,
           tags: list[str] | None = None, space_id: int | None = None) -> dict[str, Any]:
    """space_id：放入哪个知识空间（为空时放进企业空间）。同一空间内按内容去重。"""
    filename = os.path.basename(filename or "").strip() or "未命名文档"
    file_type = file_type_of(filename)
    if not data:
        raise ValueError("文件为空")
    if len(data) > KB_MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f"文件超过 {KB_MAX_FILE_MB}MB 上限")
    sha = hashlib.sha256(data).hexdigest()

    with get_connection() as conn:
        if space_id is None:
            from backend.kb.spaces import org_space_id

            space_id = org_space_id(conn)
        existing = conn.execute(text("""
            SELECT d.id, d.filename, d.status, p.slug AS page_slug FROM kb_documents d
            LEFT JOIN wiki_pages p ON p.id = d.page_id AND NOT p.is_deleted
            WHERE d.sha256 = :s AND d.space_id = :space
        """), {"s": sha, "space": space_id}).mappings().first()
    if existing and existing["page_slug"]:
        raise DuplicateDocument(dict(existing))

    path = _storage_path(sha, file_type)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    with get_connection() as conn:
        if existing:  # 之前失败或页面已删除：复用文档记录重新处理
            doc_id = existing["id"]
            conn.execute(text("UPDATE kb_documents SET filename = :f, status = 'processing', error = NULL, "
                              "uploaded_by = :u, updated_at = now() WHERE id = :id"),
                         {"f": filename, "u": user_id, "id": doc_id})
        else:
            doc_id = conn.execute(text("""
                INSERT INTO kb_documents (filename, file_type, size_bytes, sha256, storage_path, uploaded_by, space_id)
                VALUES (:f, :t, :n, :s, :p, :u, :space) RETURNING id
            """), {"f": filename, "t": file_type, "n": len(data), "s": sha, "p": path, "u": user_id,
                   "space": space_id}).scalar()

    try:
        converted = convert_to_markdown(filename, data)
    except ValueError as exc:
        with get_connection() as conn:
            conn.execute(text("UPDATE kb_documents SET status = 'failed', error = :e, updated_at = now() WHERE id = :id"),
                         {"e": str(exc), "id": doc_id})
        raise

    with get_connection() as conn:
        page = wiki.create_page(
            converted.title, converted.markdown, user_id, parent_id=parent_id,
            tags=(tags or []) + [converted.file_type.upper()],
            source_document_id=doc_id, change_note=f"导入文档 {filename}", conn=conn, space_id=space_id,
        )
        conn.execute(text("UPDATE kb_documents SET status = 'ready', page_id = :p, updated_at = now() WHERE id = :id"),
                     {"p": page["id"], "id": doc_id})

    from backend.kb.indexer import submit_index

    submit_index(page["id"])
    logger.info("文档入库: %s → 页面 %s", filename, page["slug"])
    return {"document_id": doc_id, "filename": filename, "file_type": file_type, "page": page}


def get_document_file(document_id: int, spaces: list[int] | None = None) -> tuple[str, str, str] | None:
    """spaces：调用者可读的知识空间；不在其中的文档视为不存在。"""
    with get_connection() as conn:
        row = conn.execute(text("SELECT filename, file_type, storage_path, space_id FROM kb_documents WHERE id = :id"),
                           {"id": document_id}).first()
    if row and spaces is not None and row[3] not in spaces:
        return None
    if not row or not os.path.exists(row[2]):
        return None
    return row[0], row[1], row[2]
