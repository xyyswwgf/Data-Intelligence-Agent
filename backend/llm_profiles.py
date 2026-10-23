"""
模型配置管理 — 在界面上维护多个本地 / 云端模型，随时切换

  - API Key 加密存库，接口只返回掩码；编辑时留空表示不修改
  - 一个默认模型（新对话默认使用），用户可按对话切换到其他已启用的模型
  - 连通性测试：实测一次对话 + 一次工具调用，记录延迟和是否支持工具调用
  - 模型发现：调用服务的 /models 接口列出可用模型（Ollama、vLLM、DeepSeek 等都支持）
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from sqlalchemy import text

from backend.database import get_connection
from backend.llm_gateway import LLMError, ToolSpec, describe, get_provider
from backend.secrets_store import decrypt, encrypt, mask

_FIELDS = ("name", "kind", "provider", "base_url", "model", "context_window", "max_output_tokens",
           "thinking_budget", "reasoning_effort", "no_reasoning_effort", "timeout_seconds", "enabled")


def _public(row) -> dict[str, Any]:
    data = {k: row[k] for k in ("id", *_FIELDS, "is_default", "last_test", "created_at", "updated_at")}
    data["api_key_masked"] = mask(decrypt(row["api_key_encrypted"]))
    return data


def list_profiles(enabled_only: bool = False) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT * FROM llm_profiles {"WHERE enabled" if enabled_only else ""} ORDER BY is_default DESC, kind, name
        """)).mappings().all()
    return [_public(r) for r in rows]


def options_for_users() -> dict[str, Any]:
    """普通用户可见的模型选项（不含地址和密钥）。没有配置任何模型时返回环境变量模型。"""
    items = [
        {"id": p["id"], "name": p["name"], "kind": p["kind"], "model": p["model"], "is_default": p["is_default"],
         "supports_tools": (p["last_test"] or {}).get("tools")}
        for p in list_profiles(enabled_only=True)
    ]
    if not items:
        env = describe(get_provider())
        items = [{"id": None, "name": "系统默认模型", "kind": env["kind"], "model": env["model"],
                  "is_default": True, "supports_tools": None, "configured": env["configured"]}]
    return {"items": items}


def _validate(data: dict) -> None:
    if data.get("provider") not in (None, "anthropic", "openai_compatible"):
        raise ValueError("协议必须是 anthropic 或 openai_compatible")
    if data.get("kind") not in (None, "local", "cloud"):
        raise ValueError("类型必须是 local 或 cloud")
    url = data.get("base_url")
    if url is not None and not url.startswith(("http://", "https://")):
        raise ValueError("接口地址必须以 http:// 或 https:// 开头")
    for key in ("reasoning_effort", "no_reasoning_effort"):
        if data.get(key) not in (None, "", "none", "minimal", "low", "medium", "high"):
            raise ValueError(f"{key} 只能是 none / minimal / low / medium / high 或留空")


def create_profile(data: dict) -> dict[str, Any]:
    _validate(data)
    params = {k: data.get(k) for k in _FIELDS}
    params["base_url"] = params["base_url"].rstrip("/")
    params["api_key"] = encrypt(data.get("api_key"))
    with get_connection() as conn:
        if conn.execute(text("SELECT 1 FROM llm_profiles WHERE name = :n"), {"n": params["name"]}).first():
            raise ValueError(f"已存在名为 {params['name']} 的模型配置")
        first = not conn.execute(text("SELECT EXISTS (SELECT 1 FROM llm_profiles)")).scalar()
        row = conn.execute(text(f"""
            INSERT INTO llm_profiles ({", ".join(_FIELDS)}, api_key_encrypted, is_default)
            VALUES ({", ".join(":" + f for f in _FIELDS)}, :api_key, :is_default)
            RETURNING *
        """), {**params, "is_default": first}).mappings().one()
    return _public(row)


def update_profile(profile_id: int, data: dict) -> dict[str, Any]:
    _validate(data)
    sets, params = ["updated_at = now()"], {"id": profile_id}
    for key in _FIELDS:
        if data.get(key) is not None:
            sets.append(f"{key} = :{key}")
            params[key] = data[key].rstrip("/") if key == "base_url" else data[key]
    if data.get("api_key") is not None:  # 空字符串表示清除，None 表示不修改
        sets.append("api_key_encrypted = :api_key")
        params["api_key"] = encrypt(data["api_key"])
    with get_connection() as conn:
        row = conn.execute(text(f"UPDATE llm_profiles SET {', '.join(sets)} WHERE id = :id RETURNING *"), params).mappings().first()
        if not row:
            raise LookupError("模型配置不存在")
        if row["is_default"] and not row["enabled"]:
            raise ValueError("默认模型不能停用，请先把其他模型设为默认")
    return _public(row)


def set_default(profile_id: int) -> None:
    with get_connection() as conn:
        enabled = conn.execute(text("SELECT enabled FROM llm_profiles WHERE id = :id"), {"id": profile_id}).scalar()
        if enabled is None:
            raise LookupError("模型配置不存在")
        if not enabled:
            raise ValueError("已停用的模型不能设为默认")
        conn.execute(text("UPDATE llm_profiles SET is_default = FALSE WHERE is_default"))
        conn.execute(text("UPDATE llm_profiles SET is_default = TRUE, updated_at = now() WHERE id = :id"), {"id": profile_id})


def delete_profile(profile_id: int) -> None:
    with get_connection() as conn:
        row = conn.execute(text("DELETE FROM llm_profiles WHERE id = :id RETURNING is_default"), {"id": profile_id}).first()
        if not row:
            raise LookupError("模型配置不存在")
        if row[0]:  # 删除了默认模型：把剩下的第一个启用模型设为默认
            conn.execute(text("""
                UPDATE llm_profiles SET is_default = TRUE
                WHERE id = (SELECT id FROM llm_profiles WHERE enabled ORDER BY id LIMIT 1)
            """))


_PROBE_TOOL = ToolSpec("get_time", "返回当前时间。用户询问时间时必须调用。",
                       {"type": "object", "properties": {"timezone": {"type": "string"}}, "required": ["timezone"]})


def test_profile(profile_id: int) -> dict[str, Any]:
    """实测：一次普通对话（延迟、能否回复）+ 一次工具调用（是否支持 Function Calling）。"""
    provider = get_provider(profile_id)
    if provider.profile_id != profile_id:
        raise LookupError("模型配置不存在或已停用")
    result: dict[str, Any] = {"ok": False, "tools": None, "tested_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    started = time.perf_counter()
    try:
        reply = provider.chat("你是连通性测试助手，只回复 OK。", [{"role": "user", "content": "ping"}], max_tokens=64)
        result |= {"ok": True, "latency_ms": int((time.perf_counter() - started) * 1000),
                   "reply": (reply.text or reply.reasoning)[:80], "reasoning": bool(reply.reasoning)}
        try:
            probe = provider.chat("需要时调用工具。", [{"role": "user", "content": "北京现在几点？"}],
                                  tools=[_PROBE_TOOL], max_tokens=256)
            result["tools"] = bool(probe.tool_calls)
        except LLMError as exc:
            result["tools"] = False
            result["tools_error"] = str(exc)[:200]
    except LLMError as exc:
        result["error"] = str(exc)[:300]
    with get_connection() as conn:
        conn.execute(text("UPDATE llm_profiles SET last_test = CAST(:t AS jsonb) WHERE id = :id"),
                     {"t": json.dumps(result, ensure_ascii=False), "id": profile_id})
    return result


def discover_models(provider: str, base_url: str, api_key: str | None = None, profile_id: int | None = None) -> list[str]:
    """列出服务端可用的模型名。编辑已有配置时可不传 Key，复用已保存的 Key。"""
    if not api_key and profile_id:
        with get_connection() as conn:
            stored = conn.execute(text("SELECT api_key_encrypted FROM llm_profiles WHERE id = :id"), {"id": profile_id}).scalar()
        api_key = decrypt(stored)
    base = base_url.rstrip("/")
    headers = {}
    if provider == "anthropic":
        headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
        url = f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
    else:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{base}/models"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers=headers)
    except Exception as exc:
        raise ValueError(f"无法连接 {url}: {exc}") from exc
    if resp.status_code >= 400:
        raise ValueError(f"{url} 返回 {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    items = data.get("data") or data.get("models") or []
    names = [str(m.get("id") or m.get("name")) for m in items if isinstance(m, dict) and (m.get("id") or m.get("name"))]
    return sorted(set(names))
