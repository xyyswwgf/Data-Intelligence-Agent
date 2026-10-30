"""
知识空间：知识库页面和文档的归属与权限

  - 企业空间（org）：本企业所有成员可读；编辑及以上角色可写，删除需要管理员
  - 个人空间（personal）：每个成员一个，首次使用时自动创建；只有本人可读写（管理员也看不到）
  - 部门 / 门店空间（team）：表结构已支持，暂未开放

读接口一律传入 readable_spaces(user) 的结果做过滤；spaces=None 只给系统内部任务使用（建索引、门店数据导入）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.database import get_connection

_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def _org_of(conn: Connection, user_id: int) -> int:
    org = conn.execute(text("SELECT org_id FROM users WHERE id = :u"), {"u": user_id}).scalar()
    if org is None:
        raise LookupError("用户不存在")
    return org


def org_space_id(conn: Connection, org_id: int | None = None) -> int:
    """企业空间；org_id 为空时取默认（第一个）企业。"""
    if org_id is None:
        org_id = conn.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
    space = conn.execute(text("SELECT id FROM kb_spaces WHERE org_id = :o AND kind = 'org'"), {"o": org_id}).scalar()
    if space is None:
        space = conn.execute(text("""
            INSERT INTO kb_spaces (org_id, kind, name) VALUES (:o, 'org', '企业知识库')
            ON CONFLICT (org_id) WHERE kind = 'org' DO UPDATE SET name = kb_spaces.name RETURNING id
        """), {"o": org_id}).scalar()
    return space


def personal_space_id(conn: Connection, user_id: int) -> int:
    space = conn.execute(text("SELECT id FROM kb_spaces WHERE owner_id = :u AND kind = 'personal'"), {"u": user_id}).scalar()
    if space is None:
        space = conn.execute(text("""
            INSERT INTO kb_spaces (org_id, kind, name, owner_id) VALUES (:o, 'personal', '我的知识库', :u)
            ON CONFLICT (owner_id) WHERE kind = 'personal' DO UPDATE SET name = kb_spaces.name RETURNING id
        """), {"o": _org_of(conn, user_id), "u": user_id}).scalar()
    return space


def readable_spaces(user_id: int) -> list[int]:
    """用户能读的空间：本企业的企业空间 + 自己的个人空间。"""
    with get_connection() as conn:
        return [org_space_id(conn, _org_of(conn, user_id)), personal_space_id(conn, user_id)]


def list_spaces(user_id: int, role: str) -> list[dict[str, Any]]:
    readable = readable_spaces(user_id)
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT s.id, s.kind, s.name,
                   (SELECT COUNT(*) FROM wiki_pages p WHERE p.space_id = s.id AND NOT p.is_deleted) AS pages
            FROM kb_spaces s WHERE s.id = ANY(:ids)
            ORDER BY CASE s.kind WHEN 'org' THEN 0 WHEN 'team' THEN 1 ELSE 2 END
        """), {"ids": readable}).mappings().all()
    return [{**dict(r), "can_write": r["kind"] == "personal" or _ROLE_RANK.get(role, 0) >= 1,
             "can_delete": r["kind"] == "personal" or role == "admin"} for r in rows]


def space_info(space_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(text("SELECT id, org_id, kind, name, owner_id FROM kb_spaces WHERE id = :s"),
                           {"s": space_id}).mappings().first()
    return dict(row) if row else None


def can_read(user_id: int, space_id: int) -> bool:
    return space_id in readable_spaces(user_id)


def can_write(user_id: int, role: str, space_id: int) -> bool:
    if not can_read(user_id, space_id):
        return False
    space = space_info(space_id)
    return space["kind"] == "personal" or _ROLE_RANK.get(role, 0) >= 1


def can_delete(user_id: int, role: str, space_id: int) -> bool:
    if not can_read(user_id, space_id):
        return False
    space = space_info(space_id)
    return space["kind"] == "personal" or role == "admin"


def page_space(page_id: int) -> int | None:
    with get_connection() as conn:
        return conn.execute(text("SELECT space_id FROM wiki_pages WHERE id = :id AND NOT is_deleted"), {"id": page_id}).scalar()
