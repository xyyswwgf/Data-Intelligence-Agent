"""
发现（市场）：模型、技能、工具的统一目录

目前目录来自本系统已接入的资源（管理员在「设置」中上架 = 启用）；
之后接入 AI 盒子时，在 _external_catalog() 中合并盒子提供的条目即可，前端不用改。

用户可以在「发现」中为自己停用某个模型、技能或工具（默认全部可用）：
  - 停用的模型不出现在对话的模型选择里（系统默认模型不能停用）
  - 停用的技能、自定义工具不会提供给智能助手（内置工具是助手的基本能力，不能停用）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.database import get_connection

KINDS = ("model", "skill", "tool")
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def disabled_keys(user_id: int, kind: str) -> set[str]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT item_key FROM user_market_prefs WHERE user_id = :u AND kind = :k AND NOT enabled
        """), {"u": user_id, "k": kind}).scalars().all()
    return set(rows)


def _models(off: set[str]) -> list[dict[str, Any]]:
    from backend.config import BILLING_DEFAULT_PRICE_INPUT, BILLING_DEFAULT_PRICE_OUTPUT
    from backend.llm_profiles import list_profiles

    items = []
    for p in list_profiles(enabled_only=True):
        test = p.get("last_test") or {}
        price_in = p.get("price_input")
        price_out = p.get("price_output")
        items.append({
            "kind": "model", "key": str(p["id"]), "name": p["name"], "description": p.get("description") or "",
            "badges": ["本地" if p["kind"] == "local" else "云端"] + (["支持工具调用"] if test.get("tools") else []),
            "meta": {"model": p["model"], "context_window": p["context_window"], "is_default": p["is_default"],
                     "price_input": float(price_in if price_in is not None else BILLING_DEFAULT_PRICE_INPUT),
                     "price_output": float(price_out if price_out is not None else BILLING_DEFAULT_PRICE_OUTPUT)},
            "locked": bool(p["is_default"]), "enabled": str(p["id"]) not in off, "source": "local",
        })
    return items


def _skills(off: set[str]) -> list[dict[str, Any]]:
    from backend.assistant.skills import list_skills

    return [{
        "kind": "skill", "key": s["name"], "name": s["title"], "description": s["description"],
        "badges": ["技能"], "meta": {"tools": s.get("tools") or []},
        "locked": False, "enabled": s["name"] not in off, "source": "local",
    } for s in list_skills(enabled_only=True)]


def _tools(off: set[str], role: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text("SELECT name, display_name, description, kind, min_role FROM agent_tools "
                                 "WHERE enabled ORDER BY kind, id")).mappings().all()
    items = []
    for t in rows:
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(t["min_role"], 0):
            continue
        builtin = t["kind"] == "builtin"
        items.append({
            "kind": "tool", "key": t["name"], "name": t["display_name"], "description": t["description"],
            "badges": ["内置" if builtin else ("HTTP 接口" if t["kind"] == "http" else "SQL 查询")],
            "meta": {"min_role": t["min_role"]},
            "locked": builtin, "enabled": builtin or t["name"] not in off, "source": "local",
        })
    return items


def _external_catalog() -> dict[str, Any]:
    """AI 盒子目录（预留）。接入后返回 {"connected": True, "items": [...]}，条目格式与本地条目一致。"""
    return {"connected": False, "name": "AI 盒子", "items": []}


def catalog(user_id: int, role: str) -> dict[str, Any]:
    return {
        "models": _models(disabled_keys(user_id, "model")),
        "skills": _skills(disabled_keys(user_id, "skill")),
        "tools": _tools(disabled_keys(user_id, "tool"), role),
        "external": _external_catalog(),
    }


def set_enabled(user_id: int, role: str, kind: str, key: str, enabled: bool) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("类型只能是 model / skill / tool")
    items = {"model": lambda: _models(set()), "skill": lambda: _skills(set()), "tool": lambda: _tools(set(), role)}[kind]()
    item = next((i for i in items if i["key"] == key), None)
    if item is None:
        raise LookupError("条目不存在或你没有权限使用")
    if item["locked"] and not enabled:
        raise ValueError("系统默认模型和内置工具不能停用")
    with get_connection() as conn:
        conn.execute(text("""
            INSERT INTO user_market_prefs (user_id, kind, item_key, enabled) VALUES (:u, :k, :key, :e)
            ON CONFLICT (user_id, kind, item_key) DO UPDATE SET enabled = :e, updated_at = now()
        """), {"u": user_id, "k": kind, "key": key, "e": enabled})
    return {**item, "enabled": enabled}
