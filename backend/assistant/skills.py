"""
技能（Skills）— 可复用的「做事方法」，借鉴 Anthropic Agent Skills 的渐进式加载

  每个技能 = 名称 + 适用场景描述 + 详细指引（Markdown）+ 推荐工具
  - 系统提示里只放所有技能的「名称 + 适用场景」，几十个技能也只占很少的上下文
  - 模型判断需要时调用 use_skill 加载完整指引，再按指引执行
  - 用户也可以在输入框手动指定技能，此时指引直接注入本轮上下文
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from backend.database import get_connection

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,40}$")


def list_skills(enabled_only: bool = False) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT id, name, title, description, instructions, tools, enabled, created_at, updated_at
            FROM agent_skills {"WHERE enabled" if enabled_only else ""} ORDER BY title
        """)).mappings().all()
    return [dict(r) for r in rows]


def get_skill(name: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(text("SELECT * FROM agent_skills WHERE name = :n AND enabled"), {"n": name}).mappings().first()
    return dict(row) if row else None


def _validate(data: dict, creating: bool) -> None:
    if creating and not NAME_PATTERN.match(data.get("name") or ""):
        raise ValueError("技能标识需为 3–41 位小写字母、数字或连字符，并以字母开头")
    for key, limit in (("title", 100), ("description", 500), ("instructions", 20000)):
        value = data.get(key)
        if creating and not (value or "").strip():
            raise ValueError(f"{key} 不能为空")
        if value is not None and len(value) > limit:
            raise ValueError(f"{key} 不能超过 {limit} 字")


def create_skill(data: dict, user_id: int | None) -> dict[str, Any]:
    _validate(data, True)
    with get_connection() as conn:
        if conn.execute(text("SELECT 1 FROM agent_skills WHERE name = :n"), {"n": data["name"]}).first():
            raise ValueError(f"技能 {data['name']} 已存在")
        row = conn.execute(text("""
            INSERT INTO agent_skills (name, title, description, instructions, tools, enabled, created_by)
            VALUES (:name, :title, :description, :instructions, :tools, :enabled, :uid) RETURNING *
        """), {"name": data["name"], "title": data["title"].strip(), "description": data["description"].strip(),
               "instructions": data["instructions"], "tools": list(data.get("tools") or []),
               "enabled": data.get("enabled", True), "uid": user_id}).mappings().one()
    return dict(row)


def update_skill(skill_id: int, data: dict) -> dict[str, Any]:
    _validate(data, False)
    sets, params = ["updated_at = now()"], {"id": skill_id}
    for key in ("title", "description", "instructions", "tools", "enabled"):
        if data.get(key) is not None:
            sets.append(f"{key} = :{key}")
            params[key] = list(data[key]) if key == "tools" else data[key]
    with get_connection() as conn:
        row = conn.execute(text(f"UPDATE agent_skills SET {', '.join(sets)} WHERE id = :id RETURNING *"), params).mappings().first()
    if not row:
        raise LookupError("技能不存在")
    return dict(row)


def delete_skill(skill_id: int) -> None:
    with get_connection() as conn:
        if not conn.execute(text("DELETE FROM agent_skills WHERE id = :id"), {"id": skill_id}).rowcount:
            raise LookupError("技能不存在")


def catalog_prompt(skills: list[dict]) -> str:
    if not skills:
        return ""
    lines = [f"- {s['name']}（{s['title']}）：{s['description']}" for s in skills]
    return ("\n\n## 可用技能\n以下技能封装了特定任务的做法。判断用户的请求符合某个技能的适用场景时，"
            "先调用 use_skill 加载该技能的详细指引，再按指引执行：\n" + "\n".join(lines))


def render_instructions(skill: dict) -> str:
    tools = f"\n\n推荐使用的工具：{', '.join(skill['tools'])}" if skill.get("tools") else ""
    return f"# 技能：{skill['title']}\n\n{skill['instructions']}{tools}"
