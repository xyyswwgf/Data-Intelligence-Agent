"""管理员：模型配置、工具、技能管理 API。"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend import auth, llm_profiles
from backend.assistant import skills as skill_store
from backend.assistant.custom_tools import (
    NAME_PATTERN,
    ToolConfigError,
    validate_http_config,
    validate_schema,
    validate_sql_config,
)
from backend.auth import CurrentUser, require_role
from backend.database import get_connection
from backend.secrets_store import decrypt, encrypt, mask

router = APIRouter(prefix="/api/admin", tags=["agent-admin"])
Admin = Depends(require_role("admin"))


def _errors(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# 模型配置
# ---------------------------------------------------------------------------


class ProfileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: Literal["local", "cloud"] = "cloud"
    provider: Literal["anthropic", "openai_compatible"] = "openai_compatible"
    base_url: str = Field(..., min_length=8, max_length=500)
    api_key: str | None = Field(None, max_length=500)
    model: str = Field(..., min_length=1, max_length=200)
    context_window: int = Field(64000, ge=2000, le=2_000_000)
    max_output_tokens: int = Field(2048, ge=256, le=64000)
    thinking_budget: int = Field(0, ge=0, le=64000)
    reasoning_effort: str = ""
    no_reasoning_effort: str = ""
    timeout_seconds: int = Field(60, ge=5, le=600)
    enabled: bool = True
    description: str = Field("", max_length=500)
    price_input: float | None = Field(None, ge=0, le=10000, description="每 1K 输入 token 的点数，留空用系统默认价")
    price_output: float | None = Field(None, ge=0, le=10000, description="每 1K 输出 token 的点数")


class ProfilePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    kind: Literal["local", "cloud"] | None = None
    provider: Literal["anthropic", "openai_compatible"] | None = None
    base_url: str | None = Field(None, min_length=8, max_length=500)
    api_key: str | None = Field(None, max_length=500, description="留空不修改；传空字符串清除")
    model: str | None = Field(None, min_length=1, max_length=200)
    context_window: int | None = Field(None, ge=2000, le=2_000_000)
    max_output_tokens: int | None = Field(None, ge=256, le=64000)
    thinking_budget: int | None = Field(None, ge=0, le=64000)
    reasoning_effort: str | None = None
    no_reasoning_effort: str | None = None
    timeout_seconds: int | None = Field(None, ge=5, le=600)
    enabled: bool | None = None
    description: str | None = Field(None, max_length=500)
    price_input: float | None = Field(None, ge=0, le=10000)
    price_output: float | None = Field(None, ge=0, le=10000)


class DiscoverIn(BaseModel):
    provider: Literal["anthropic", "openai_compatible"] = "openai_compatible"
    base_url: str = Field(..., min_length=8, max_length=500)
    api_key: str | None = None
    profile_id: int | None = None


@router.get("/models")
def list_models(user: CurrentUser = Admin):
    return {"items": llm_profiles.list_profiles()}


@router.post("/models", status_code=201)
def create_model(req: ProfileIn, user: CurrentUser = Admin):
    created = _errors(llm_profiles.create_profile, req.model_dump())
    auth.audit("model_create", user, "llm_profile", created["id"], {"name": created["name"], "model": created["model"]})
    return created


@router.patch("/models/{profile_id}")
def update_model(profile_id: int, req: ProfilePatch, user: CurrentUser = Admin):
    updated = _errors(llm_profiles.update_profile, profile_id, req.model_dump(exclude_unset=True))
    changes = req.model_dump(exclude_unset=True)
    if "api_key" in changes:
        changes["api_key"] = "******"
    auth.audit("model_update", user, "llm_profile", profile_id, changes)
    return updated


@router.post("/models/{profile_id}/default")
def default_model(profile_id: int, user: CurrentUser = Admin):
    _errors(llm_profiles.set_default, profile_id)
    auth.audit("model_default", user, "llm_profile", profile_id)
    return {"ok": True}


@router.delete("/models/{profile_id}")
def delete_model(profile_id: int, user: CurrentUser = Admin):
    _errors(llm_profiles.delete_profile, profile_id)
    auth.audit("model_delete", user, "llm_profile", profile_id)
    return {"ok": True}


@router.post("/models/{profile_id}/test")
def test_model(profile_id: int, user: CurrentUser = Admin):
    return _errors(llm_profiles.test_profile, profile_id)


@router.post("/models/discover")
def discover(req: DiscoverIn, user: CurrentUser = Admin):
    return {"models": _errors(llm_profiles.discover_models, req.provider, req.base_url, req.api_key, req.profile_id)}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


class ToolIn(BaseModel):
    name: str = Field(..., min_length=3, max_length=41)
    display_name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=5, max_length=1000)
    kind: Literal["http", "sql"]
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    config: dict = Field(default_factory=dict)
    secret_headers: dict[str, str] | None = Field(None, description="加密保存的请求头，如 Authorization")
    min_role: Literal["viewer", "editor", "admin"] = "viewer"
    enabled: bool = True


class ToolPatch(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, min_length=5, max_length=1000)
    parameters: dict | None = None
    config: dict | None = None
    secret_headers: dict[str, str] | None = None
    min_role: Literal["viewer", "editor", "admin"] | None = None
    enabled: bool | None = None


class ToolTest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


def _tool_public(row) -> dict:
    data = {k: row[k] for k in ("id", "name", "display_name", "description", "kind", "parameters", "config",
                                "min_role", "enabled", "created_at", "updated_at")}
    secrets = json.loads(decrypt(row["secrets_encrypted"]) or "{}")
    data["secret_headers"] = {k: mask(v) for k, v in secrets.items()}
    return data


def _validated(kind: str, parameters: dict, config: dict) -> tuple[dict, dict]:
    try:
        schema = validate_schema(parameters)
        cfg = validate_http_config(config) if kind == "http" else validate_sql_config(config, schema)
    except ToolConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return schema, cfg


@router.get("/tools")
def list_tools(user: CurrentUser = Admin):
    with get_connection() as conn:
        rows = conn.execute(text("SELECT * FROM agent_tools ORDER BY kind <> 'builtin', id")).mappings().all()
    return {"items": [_tool_public(r) for r in rows]}


@router.post("/tools", status_code=201)
def create_tool(req: ToolIn, user: CurrentUser = Admin):
    if not NAME_PATTERN.match(req.name):
        raise HTTPException(status_code=400, detail="工具标识需为 3–41 位小写字母、数字或下划线，并以字母开头")
    schema, cfg = _validated(req.kind, req.parameters, req.config)
    with get_connection() as conn:
        if conn.execute(text("SELECT 1 FROM agent_tools WHERE name = :n"), {"n": req.name}).first():
            raise HTTPException(status_code=400, detail=f"工具 {req.name} 已存在")
        row = conn.execute(text("""
            INSERT INTO agent_tools (name, display_name, description, kind, parameters, config, secrets_encrypted,
                                     min_role, enabled, created_by)
            VALUES (:name, :dn, :desc, :kind, CAST(:params AS jsonb), CAST(:cfg AS jsonb), :sec, :role, :enabled, :uid)
            RETURNING *
        """), {"name": req.name, "dn": req.display_name, "desc": req.description, "kind": req.kind,
               "params": json.dumps(schema, ensure_ascii=False), "cfg": json.dumps(cfg, ensure_ascii=False),
               "sec": encrypt(json.dumps(req.secret_headers)) if req.secret_headers else None,
               "role": req.min_role, "enabled": req.enabled, "uid": user.id}).mappings().one()
    auth.audit("tool_create", user, "agent_tool", row["id"], {"name": req.name, "kind": req.kind})
    return _tool_public(row)


@router.patch("/tools/{tool_id}")
def update_tool(tool_id: int, req: ToolPatch, user: CurrentUser = Admin):
    with get_connection() as conn:
        current = conn.execute(text("SELECT * FROM agent_tools WHERE id = :id"), {"id": tool_id}).mappings().first()
        if not current:
            raise HTTPException(status_code=404, detail="工具不存在")
        changes = req.model_dump(exclude_unset=True)
        if current["kind"] == "builtin" and set(changes) - {"enabled", "min_role"}:
            raise HTTPException(status_code=400, detail="内置工具只能启用/停用或调整使用权限")
        sets, params = ["updated_at = now()"], {"id": tool_id}
        if "parameters" in changes or "config" in changes:
            schema, cfg = _validated(current["kind"], changes.get("parameters", current["parameters"]),
                                     changes.get("config", current["config"]))
            sets += ["parameters = CAST(:params AS jsonb)", "config = CAST(:cfg AS jsonb)"]
            params |= {"params": json.dumps(schema, ensure_ascii=False), "cfg": json.dumps(cfg, ensure_ascii=False)}
        for key in ("display_name", "description", "min_role", "enabled"):
            if key in changes:
                sets.append(f"{key} = :{key}")
                params[key] = changes[key]
        if "secret_headers" in changes:
            sets.append("secrets_encrypted = :sec")
            params["sec"] = encrypt(json.dumps(changes["secret_headers"])) if changes["secret_headers"] else None
        row = conn.execute(text(f"UPDATE agent_tools SET {', '.join(sets)} WHERE id = :id RETURNING *"), params).mappings().one()
    if "secret_headers" in changes:
        changes["secret_headers"] = "******"
    auth.audit("tool_update", user, "agent_tool", tool_id, changes)
    return _tool_public(row)


@router.delete("/tools/{tool_id}")
def delete_tool(tool_id: int, user: CurrentUser = Admin):
    with get_connection() as conn:
        row = conn.execute(text("SELECT kind, name FROM agent_tools WHERE id = :id"), {"id": tool_id}).first()
        if not row:
            raise HTTPException(status_code=404, detail="工具不存在")
        if row[0] == "builtin":
            raise HTTPException(status_code=400, detail="内置工具不能删除，可以停用")
        conn.execute(text("DELETE FROM agent_tools WHERE id = :id"), {"id": tool_id})
    auth.audit("tool_delete", user, "agent_tool", tool_id, {"name": row[1]})
    return {"ok": True}


@router.post("/tools/{tool_id}/test")
def test_tool(tool_id: int, req: ToolTest, user: CurrentUser = Admin):
    """用给定参数实际执行一次工具，方便管理员在上线前确认配置正确。"""
    from backend.assistant.tools import Registry, ToolContext, execute
    from backend.llm_gateway import ToolSpec

    with get_connection() as conn:
        row = conn.execute(text("SELECT * FROM agent_tools WHERE id = :id"), {"id": tool_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="工具不存在")
    spec = ToolSpec(row["name"], row["description"], row["parameters"])
    registry = Registry([spec], {} if row["kind"] == "builtin" else {row["name"]: dict(row)})
    ctx = ToolContext(user.id, "", False, role=user.role)
    result = execute(row["name"], req.args, ctx, registry)
    return {"ok": result.ok, "summary": result.summary, "for_model": result.for_model, "display": result.display}


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------


class SkillIn(BaseModel):
    name: str = Field(..., min_length=3, max_length=41)
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=5, max_length=500)
    instructions: str = Field(..., min_length=10, max_length=20000)
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True


class SkillPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, min_length=5, max_length=500)
    instructions: str | None = Field(None, min_length=10, max_length=20000)
    tools: list[str] | None = None
    enabled: bool | None = None


@router.get("/skills")
def list_skills(user: CurrentUser = Admin):
    return {"items": skill_store.list_skills()}


@router.post("/skills", status_code=201)
def create_skill(req: SkillIn, user: CurrentUser = Admin):
    created = _errors(skill_store.create_skill, req.model_dump(), user.id)
    auth.audit("skill_create", user, "agent_skill", created["id"], {"name": created["name"]})
    return created


@router.patch("/skills/{skill_id}")
def update_skill(skill_id: int, req: SkillPatch, user: CurrentUser = Admin):
    updated = _errors(skill_store.update_skill, skill_id, req.model_dump(exclude_unset=True))
    auth.audit("skill_update", user, "agent_skill", skill_id, {k: v for k, v in req.model_dump(exclude_unset=True).items()
                                                              if k != "instructions"})
    return updated


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: int, user: CurrentUser = Admin):
    _errors(skill_store.delete_skill, skill_id)
    auth.audit("skill_delete", user, "agent_skill", skill_id)
    return {"ok": True}
