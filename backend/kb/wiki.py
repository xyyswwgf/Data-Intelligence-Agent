"""
Wiki 服务 — 页面增删改查、版本历史、[[双向链接]]、目录树、知识库索引

页面保存时：写版本快照 → 重算出链 → 提交重新索引（切块 + 向量化）。
更新使用乐观锁（base_version），两人同时编辑时后保存者会收到冲突提示，而不是静默覆盖。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.database import get_connection

_LINK = re.compile(r"\[\[([^\[\]|#]+?)(?:\|[^\[\]]*)?\]\]")
_SLUG_UNSAFE = re.compile(r"[^\w一-鿿-]+")


class ConflictError(Exception):
    """乐观锁冲突：页面已被他人更新。"""


def slugify(title: str) -> str:
    slug = _SLUG_UNSAFE.sub("-", title.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)[:180] or "page"


def extract_links(markdown: str) -> set[str]:
    """[[目标]] 或 [[目标|显示文字]]，目标按 slug 规则归一化（标题或 slug 都能链接）。"""
    return {slugify(m.group(1)) for m in _LINK.finditer(markdown)}


def summarize(markdown: str, limit: int = 160) -> str:
    plain = re.sub(r"```[\s\S]*?```", " ", markdown)
    plain = re.sub(r"^#+\s.*$", " ", plain, flags=re.MULTILINE)
    plain = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), plain)
    plain = re.sub(r"[*_`>#|\-]+", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit]


def _unique_slug(conn: Connection, base: str, exclude_id: int | None = None) -> str:
    slug, n = base, 2
    while conn.execute(
        text("SELECT 1 FROM wiki_pages WHERE slug = :s AND (CAST(:ex AS BIGINT) IS NULL OR id <> CAST(:ex AS BIGINT))"), {"s": slug, "ex": exclude_id}
    ).first():
        slug, n = f"{base}-{n}", n + 1
    return slug


def _clean_tags(tags: list[str] | None) -> list[str]:
    seen, out = set(), []
    for tag in tags or []:
        tag = str(tag).strip().lstrip("#")[:40]
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:20]


def _check_parent(conn: Connection, page_id: int | None, parent_id: int | None, space_id: int | None = None) -> None:
    if parent_id is None:
        return
    row = conn.execute(text("SELECT id, space_id FROM wiki_pages WHERE id = :p AND NOT is_deleted"), {"p": parent_id}).first()
    if not row:
        raise ValueError("父页面不存在")
    if space_id is not None and row[1] != space_id:
        raise ValueError("父页面必须在同一个知识空间里")
    if page_id is None:
        return
    # 防止把页面挂到自己的子孙下面形成环
    cycle = conn.execute(text("""
        WITH RECURSIVE up AS (
            SELECT id, parent_id FROM wiki_pages WHERE id = :p
            UNION ALL
            SELECT w.id, w.parent_id FROM wiki_pages w JOIN up ON w.id = up.parent_id
        )
        SELECT 1 FROM up WHERE id = :self
    """), {"p": parent_id, "self": page_id}).first()
    if cycle:
        raise ValueError("不能把页面移动到它自己的子页面下")


def _sync_links(conn: Connection, page_id: int, markdown: str) -> None:
    conn.execute(text("DELETE FROM wiki_links WHERE from_page_id = :id"), {"id": page_id})
    for slug in extract_links(markdown):
        conn.execute(
            text("INSERT INTO wiki_links (from_page_id, to_slug) VALUES (:id, :s) ON CONFLICT DO NOTHING"),
            {"id": page_id, "s": slug[:200]},
        )


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------


def create_page(
    title: str,
    content_md: str,
    user_id: int | None,
    parent_id: int | None = None,
    tags: list[str] | None = None,
    slug: str | None = None,
    source_document_id: int | None = None,
    change_note: str | None = None,
    conn: Connection | None = None,
    space_id: int | None = None,
) -> dict[str, Any]:
    """space_id 为空时放进默认企业的企业空间（系统生成的页面，如门店数据导入）。"""
    title = title.strip()[:300]
    if not title:
        raise ValueError("标题不能为空")
    if conn is None:
        with get_connection() as c:
            page = create_page(title, content_md, user_id, parent_id, tags, slug, source_document_id, change_note, c,
                               space_id)
        from backend.kb.indexer import submit_index

        submit_index(page["id"])
        return page

    if space_id is None:
        from backend.kb.spaces import org_space_id

        space_id = org_space_id(conn)
    _check_parent(conn, None, parent_id, space_id)
    final_slug = _unique_slug(conn, slugify(slug or title))  # 页面地址全局唯一，跨空间的链接也不会歧义
    tags = _clean_tags(tags)
    page = conn.execute(
        text("""
            INSERT INTO wiki_pages (slug, title, parent_id, content_md, summary, tags, source_document_id,
                                    created_by, updated_by, space_id)
            VALUES (:slug, :title, :parent, :content, :summary, :tags, :doc, :uid, :uid, :space)
            RETURNING id, slug, title, version, space_id
        """),
        {"slug": final_slug, "title": title, "parent": parent_id, "content": content_md,
         "summary": summarize(content_md), "tags": tags, "doc": source_document_id, "uid": user_id, "space": space_id},
    ).mappings().one()
    conn.execute(
        text("""
            INSERT INTO wiki_page_versions (page_id, version, title, content_md, tags, change_note, edited_by)
            VALUES (:id, 1, :title, :content, :tags, :note, :uid)
        """),
        {"id": page["id"], "title": title, "content": content_md, "tags": tags,
         "note": change_note or "创建页面", "uid": user_id},
    )
    _sync_links(conn, page["id"], content_md)
    return dict(page)


def update_page(
    page_id: int,
    user_id: int | None,
    base_version: int,
    title: str | None = None,
    content_md: str | None = None,
    parent_id: int | None | type[...] = ...,
    tags: list[str] | None = None,
    change_note: str | None = None,
) -> dict[str, Any]:
    with get_connection() as conn:
        current = conn.execute(
            text("SELECT * FROM wiki_pages WHERE id = :id AND NOT is_deleted FOR UPDATE"), {"id": page_id}
        ).mappings().first()
        if not current:
            raise LookupError("页面不存在")
        if current["version"] != base_version:
            raise ConflictError(
                f"页面已被他人更新（当前版本 v{current['version']}，你编辑的是 v{base_version}），请刷新后合并修改"
            )
        new_title = (title.strip()[:300] if title is not None else current["title"]) or current["title"]
        new_content = content_md if content_md is not None else current["content_md"]
        new_tags = _clean_tags(tags) if tags is not None else list(current["tags"])
        new_parent = current["parent_id"] if parent_id is ... else parent_id
        if new_parent != current["parent_id"]:
            _check_parent(conn, page_id, new_parent, current["space_id"])
        version = current["version"] + 1
        page = conn.execute(
            text("""
                UPDATE wiki_pages
                SET title = :title, content_md = :content, summary = :summary, tags = :tags, parent_id = :parent,
                    version = :v, updated_by = :uid, updated_at = now(), index_status = 'pending', index_error = NULL
                WHERE id = :id
                RETURNING id, slug, title, version, space_id
            """),
            {"title": new_title, "content": new_content, "summary": summarize(new_content), "tags": new_tags,
             "parent": new_parent, "v": version, "uid": user_id, "id": page_id},
        ).mappings().one()
        conn.execute(
            text("""
                INSERT INTO wiki_page_versions (page_id, version, title, content_md, tags, change_note, edited_by)
                VALUES (:id, :v, :title, :content, :tags, :note, :uid)
            """),
            {"id": page_id, "v": version, "title": new_title, "content": new_content, "tags": new_tags,
             "note": (change_note or "").strip()[:300] or None, "uid": user_id},
        )
        _sync_links(conn, page_id, new_content)
    if content_md is not None or title is not None:
        from backend.kb.indexer import submit_index

        submit_index(page_id)
    return dict(page)


def delete_page(page_id: int) -> dict[str, Any]:
    """软删除：子页面上移到被删页面的父级，检索切块立即删除。"""
    with get_connection() as conn:
        page = conn.execute(
            text("UPDATE wiki_pages SET is_deleted = TRUE, updated_at = now() WHERE id = :id AND NOT is_deleted "
                 "RETURNING id, slug, title, parent_id"),
            {"id": page_id},
        ).mappings().first()
        if not page:
            raise LookupError("页面不存在")
        conn.execute(text("UPDATE wiki_pages SET parent_id = :p WHERE parent_id = :id"),
                     {"p": page["parent_id"], "id": page_id})
        conn.execute(text("DELETE FROM kb_chunks WHERE page_id = :id"), {"id": page_id})
        conn.execute(text("DELETE FROM wiki_links WHERE from_page_id = :id"), {"id": page_id})
    return dict(page)


def restore_version(page_id: int, version: int, user_id: int | None) -> dict[str, Any]:
    with get_connection() as conn:
        snapshot = conn.execute(
            text("SELECT title, content_md, tags FROM wiki_page_versions WHERE page_id = :id AND version = :v"),
            {"id": page_id, "v": version},
        ).mappings().first()
        current_version = conn.execute(
            text("SELECT version FROM wiki_pages WHERE id = :id AND NOT is_deleted"), {"id": page_id}
        ).scalar()
    if not snapshot or current_version is None:
        raise LookupError("版本不存在")
    return update_page(page_id, user_id, current_version, title=snapshot["title"], content_md=snapshot["content_md"],
                       tags=list(snapshot["tags"]), change_note=f"恢复到 v{version}")


# ---------------------------------------------------------------------------
# 读操作
# ---------------------------------------------------------------------------


def _in(spaces: list[int] | None, alias: str = "p") -> str:
    """读取范围：spaces 为 None 时不限制（系统内部任务），否则只看这些空间。"""
    return "" if spaces is None else f" AND {alias}.space_id = ANY(:spaces)"


def get_page(slug: str, spaces: list[int] | None = None) -> dict[str, Any] | None:
    with get_connection() as conn:
        page = conn.execute(text(f"""
            SELECT p.id, p.slug, p.title, p.parent_id, p.content_md, p.summary, p.tags, p.version,
                   p.index_status, p.index_error, p.created_at, p.updated_at,
                   p.space_id, sp.kind AS space_kind, sp.name AS space_name,
                   cu.display_name AS created_by_name, uu.display_name AS updated_by_name,
                   d.id AS document_id, d.filename AS document_filename, d.file_type AS document_type
            FROM wiki_pages p
            LEFT JOIN users cu ON cu.id = p.created_by
            LEFT JOIN users uu ON uu.id = p.updated_by
            LEFT JOIN kb_documents d ON d.id = p.source_document_id
            JOIN kb_spaces sp ON sp.id = p.space_id
            WHERE p.slug = :s AND NOT p.is_deleted{_in(spaces)}
        """), {"s": slug, "spaces": spaces}).mappings().first()
        if not page:
            return None
        page = dict(page)
        page["breadcrumbs"] = [dict(r) for r in conn.execute(text("""
            WITH RECURSIVE up AS (
                SELECT id, slug, title, parent_id, 0 AS depth FROM wiki_pages WHERE id = :p
                UNION ALL
                SELECT w.id, w.slug, w.title, w.parent_id, up.depth + 1 FROM wiki_pages w JOIN up ON w.id = up.parent_id
                WHERE up.depth < 20
            )
            SELECT slug, title FROM up ORDER BY depth DESC
        """), {"p": page["parent_id"]}).mappings().all()] if page["parent_id"] else []
        page["children"] = [dict(r) for r in conn.execute(text("""
            SELECT slug, title, summary, updated_at FROM wiki_pages
            WHERE parent_id = :id AND NOT is_deleted ORDER BY sort_order, title
        """), {"id": page["id"]}).mappings().all()]
        page["backlinks"] = [dict(r) for r in conn.execute(text(f"""
            SELECT DISTINCT p.slug, p.title FROM wiki_links l JOIN wiki_pages p ON p.id = l.from_page_id
            WHERE l.to_slug = :s AND NOT p.is_deleted AND p.id <> :id{_in(spaces)} ORDER BY p.title
        """), {"s": page["slug"], "id": page["id"], "spaces": spaces}).mappings().all()]
        existing = {r[0] for r in conn.execute(text(f"SELECT slug FROM wiki_pages p WHERE NOT is_deleted{_in(spaces)}"),
                                               {"spaces": spaces}).all()}
        page["missing_links"] = sorted(extract_links(page["content_md"]) - existing)
        page["chunk_count"] = conn.execute(
            text("SELECT COUNT(*) FROM kb_chunks WHERE page_id = :id"), {"id": page["id"]}
        ).scalar()
    return page


def list_tree(spaces: list[int] | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT id, slug, title, parent_id, sort_order, updated_at, index_status, space_id
            FROM wiki_pages p WHERE NOT is_deleted{_in(spaces)} ORDER BY sort_order, title
        """), {"spaces": spaces}).mappings().all()
    return [dict(r) for r in rows]


def list_versions(page_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT v.version, v.title, v.change_note, v.edited_at, u.display_name AS edited_by_name,
                   length(v.content_md) AS length
            FROM wiki_page_versions v LEFT JOIN users u ON u.id = v.edited_by
            WHERE v.page_id = :id ORDER BY v.version DESC
        """), {"id": page_id}).mappings().all()
    return [dict(r) for r in rows]


def get_version(page_id: int, version: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(text("""
            SELECT version, title, content_md, tags, change_note, edited_at
            FROM wiki_page_versions WHERE page_id = :id AND version = :v
        """), {"id": page_id, "v": version}).mappings().first()
    return dict(row) if row else None


def knowledge_index(spaces: list[int] | None = None) -> dict[str, Any]:
    """知识库总览：统计、标签云、最近更新、待完善（孤立页面/悬空链接）、文档处理状态。"""
    sp = {"spaces": spaces}
    page_in, doc_in = _in(spaces), _in(spaces, "d")
    with get_connection() as conn:
        stats = dict(conn.execute(text(f"""
            SELECT
              (SELECT COUNT(*) FROM wiki_pages p WHERE NOT is_deleted{page_in}) AS pages,
              (SELECT COUNT(*) FROM kb_documents d WHERE TRUE{doc_in}) AS documents,
              (SELECT COUNT(*) FROM kb_chunks c JOIN wiki_pages p ON p.id = c.page_id WHERE TRUE{page_in}) AS chunks,
              (SELECT COUNT(*) FROM kb_chunks c JOIN wiki_pages p ON p.id = c.page_id
               WHERE c.embedding IS NOT NULL{page_in}) AS embedded_chunks,
              (SELECT COUNT(*) FROM wiki_pages p WHERE NOT is_deleted AND index_status IN ('pending', 'indexing'){page_in}) AS indexing,
              (SELECT COUNT(*) FROM wiki_pages p WHERE NOT is_deleted AND index_status IN ('partial', 'failed'){page_in}) AS index_issues
        """), sp).mappings().one())
        tags = [dict(r) for r in conn.execute(text(f"""
            SELECT tag, COUNT(*) AS count FROM wiki_pages p, unnest(p.tags) AS tag
            WHERE NOT p.is_deleted{page_in} GROUP BY tag ORDER BY count DESC, tag LIMIT 50
        """), sp).mappings().all()]
        recent = [dict(r) for r in conn.execute(text(f"""
            SELECT p.slug, p.title, p.summary, p.updated_at, p.version, u.display_name AS updated_by_name
            FROM wiki_pages p LEFT JOIN users u ON u.id = p.updated_by
            WHERE NOT p.is_deleted{page_in} ORDER BY p.updated_at DESC LIMIT 10
        """), sp).mappings().all()]
        orphans = [dict(r) for r in conn.execute(text(f"""
            SELECT p.slug, p.title FROM wiki_pages p
            WHERE NOT p.is_deleted AND p.parent_id IS NULL AND p.slug <> 'home'{page_in}
              AND NOT EXISTS (SELECT 1 FROM wiki_links l JOIN wiki_pages s ON s.id = l.from_page_id
                              WHERE l.to_slug = p.slug AND NOT s.is_deleted)
            ORDER BY p.updated_at DESC LIMIT 20
        """), sp).mappings().all()]
        dangling = [dict(r) for r in conn.execute(text(f"""
            SELECT l.to_slug AS slug, COUNT(*) AS referenced_by FROM wiki_links l
            JOIN wiki_pages s ON s.id = l.from_page_id AND NOT s.is_deleted{_in(spaces, "s")}
            WHERE NOT EXISTS (SELECT 1 FROM wiki_pages p WHERE p.slug = l.to_slug AND NOT p.is_deleted)
            GROUP BY l.to_slug ORDER BY referenced_by DESC LIMIT 20
        """), sp).mappings().all()]
        documents = [dict(r) for r in conn.execute(text(f"""
            SELECT d.id, d.filename, d.file_type, d.size_bytes, d.status, d.error, d.created_at,
                   p.slug AS page_slug, u.display_name AS uploaded_by_name
            FROM kb_documents d
            LEFT JOIN wiki_pages p ON p.id = d.page_id AND NOT p.is_deleted
            LEFT JOIN users u ON u.id = d.uploaded_by
            WHERE TRUE{doc_in}
            ORDER BY d.id DESC LIMIT 20
        """), sp).mappings().all()]
    return {"stats": stats, "tags": tags, "recent": recent, "orphans": orphans,
            "dangling_links": dangling, "documents": documents}


def pages_by_tag(tag: str, spaces: list[int] | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT slug, title, summary, updated_at FROM wiki_pages p
            WHERE NOT is_deleted AND :t = ANY(tags){_in(spaces)} ORDER BY updated_at DESC
        """), {"t": tag, "spaces": spaces}).mappings().all()
    return [dict(r) for r in rows]
