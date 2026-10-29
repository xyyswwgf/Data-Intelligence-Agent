"""对话会话（SSE 事件流）与用户记忆 API。"""

from __future__ import annotations

import json
import time
from typing import Iterator, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend import auth
from backend.assistant import memory as memory_store
from backend.assistant import sessions
from backend.assistant.agent import run_turn
from backend.auth import CurrentUser, get_current_user
from backend.billing import accounts
from backend.billing.metering import BillingContext, metered_events

router = APIRouter(prefix="/api", tags=["chat"])


class SendMessage(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    mode: Literal["assistant", "agent", "kb", "query"] = "assistant"
    table_name: str | None = Field(None, max_length=128)
    model_profile_id: int | None = Field(None, description="本轮使用的模型配置，留空沿用会话模型 / 默认模型")
    skill: str | None = Field(None, max_length=64, description="手动指定的技能标识")


class RenameSession(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class MemoryIn(BaseModel):
    content: str = Field(..., min_length=2, max_length=500)
    category: Literal["preference", "fact", "goal", "instruction"] = "fact"
    pinned: bool = False


class MemoryPatch(BaseModel):
    content: str | None = Field(None, min_length=2, max_length=500)
    category: Literal["preference", "fact", "goal", "instruction"] | None = None
    pinned: bool | None = None


class MemorySettings(BaseModel):
    enabled: bool


def _user_dict(user: CurrentUser) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}


def _require_session(user: CurrentUser, session_id: str) -> dict:
    try:
        session = sessions.get_session(user.id, session_id)
    except Exception:
        session = None
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


@router.get("/chat/sessions")
def list_sessions(user: CurrentUser = Depends(get_current_user)):
    return {"items": sessions.list_sessions(user.id)}


@router.post("/chat/sessions", status_code=201)
def create_session(user: CurrentUser = Depends(get_current_user)):
    return sessions.create_session(user.id)


@router.get("/chat/sessions/{session_id}")
def get_session(session_id: str, user: CurrentUser = Depends(get_current_user)):
    session = _require_session(user, session_id)
    session.pop("user_id", None)
    return {**session, "messages": sessions.get_messages(session_id)}


@router.patch("/chat/sessions/{session_id}")
def rename_session(session_id: str, req: RenameSession, user: CurrentUser = Depends(get_current_user)):
    _require_session(user, session_id)
    sessions.rename_session(user.id, session_id, req.title)
    return {"ok": True}


@router.delete("/chat/sessions/{session_id}")
def delete_session(session_id: str, user: CurrentUser = Depends(get_current_user)):
    _require_session(user, session_id)
    sessions.delete_session(user.id, session_id)
    return {"ok": True}


def _run_mode(user: CurrentUser, session: dict, req: SendMessage) -> Iterator[dict]:
    """非推理模式（经营分析 / 知识问答 / SQL 查询）同样记录进会话，刷新后可完整回看。"""
    from backend.agent_service import run_analysis
    from backend.generic_table_agent import run_table_analysis
    from backend.kb import rag
    from backend.llm_gateway import get_provider
    from backend.nl2sql import answer_query

    sid = session["id"]
    user_msg = sessions.add_message(sid, "user", req.content, req.mode, payload={"table": req.table_name})
    if session["message_count"] == 0:
        yield {"type": "session", "title": sessions.auto_title(sid, req.content)}
    started = time.perf_counter()
    provider = get_provider(req.model_profile_id or session.get("model_profile_id"))
    try:
        if req.mode == "agent":
            result = (run_table_analysis(req.content, req.table_name, user_id=user.id) if req.table_name
                      else run_analysis(req.content, user_id=user.id))
            text = (result.get("report") or {}).get("summary", "")
        elif req.mode == "kb":
            result = rag.ask(req.content, provider=provider)
            text = result["answer"]
        else:
            result = answer_query(req.content, provider=provider)
            text = f"查询返回 {result['row_count']} 行。"
        error = None
    except Exception as exc:
        result, text, error = None, f"处理失败：{exc}", str(exc)
    msg = sessions.add_message(sid, "assistant", text, req.mode, payload={"result": result, "error": error,
                                                                           "elapsed_ms": int((time.perf_counter() - started) * 1000)})
    yield {"type": "done", "message_id": msg["id"], "seq": msg["seq"], "user_seq": user_msg["seq"], "mode": req.mode,
           "content": text, "result": result, "error": error}


@router.post("/chat/sessions/{session_id}/messages")
def send_message(session_id: str, req: SendMessage, user: CurrentUser = Depends(get_current_user)):
    session = _require_session(user, session_id)
    try:
        accounts.ensure_can_use(user.id, user.role)
    except accounts.QuotaExceeded as exc:
        raise HTTPException(status_code=402, detail=str(exc))
    if req.mode == "assistant":
        events = run_turn(_user_dict(user), session_id, req.content, req.table_name, req.model_profile_id, req.skill)
    else:
        events = _run_mode(user, session, req)
    auth.audit("chat_message", user, "chat_session", session_id, {"mode": req.mode, "question": req.content[:200]})
    # 本轮内所有大模型调用都记到当前用户名下（先扣方案额度，再扣余额）
    events = metered_events(events, BillingContext(user.id, session_id, req.mode))
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/chat/sessions")
def delete_all_sessions(user: CurrentUser = Depends(get_current_user)):
    """隐私设置：删除自己的全部对话（长期记忆、使用量记录不受影响）。"""
    count = sessions.delete_all_sessions(user.id)
    auth.audit("chat_clear", user, "chat_session", None, {"deleted": count})
    return {"deleted": count}


@router.get("/me/export")
def export_my_data(user: CurrentUser = Depends(get_current_user)):
    """隐私设置：导出自己的对话、记忆、使用量与账单（JSON）。"""
    from backend.billing.accounts import list_transactions, usage_report

    data = {
        "user": _user_dict(user),
        "sessions": [{**s, "messages": sessions.get_messages(s["id"])} for s in sessions.list_sessions(user.id, limit=1000)],
        "memories": memory_store.list_memories(user.id),
        "usage": usage_report(user.id, days=365),
        "transactions": list_transactions(user.id, limit=500),
    }
    auth.audit("data_export", user, "user", str(user.id), {"sessions": len(data["sessions"])})
    body = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="datamind-{user.username}.json"'})


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------


@router.get("/memories")
def list_memories(user: CurrentUser = Depends(get_current_user)):
    return {"enabled": memory_store.memory_enabled_for(user.id), "items": memory_store.list_memories(user.id)}


@router.post("/memories", status_code=201)
def add_memory(req: MemoryIn, user: CurrentUser = Depends(get_current_user)):
    try:
        saved = memory_store.save_memory(user.id, req.content, req.category, "manual", pinned=req.pinned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return saved


@router.patch("/memories/{memory_id}")
def patch_memory(memory_id: int, req: MemoryPatch, user: CurrentUser = Depends(get_current_user)):
    try:
        return memory_store.update_memory(user.id, memory_id, req.content, req.category, req.pinned)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/memories/{memory_id}")
def remove_memory(memory_id: int, user: CurrentUser = Depends(get_current_user)):
    try:
        memory_store.delete_memory(user.id, memory_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


@router.delete("/memories")
def clear_memories(user: CurrentUser = Depends(get_current_user)):
    count = memory_store.clear_memories(user.id)
    auth.audit("memory_clear", user, "memory", None, {"deleted": count})
    return {"deleted": count}


@router.put("/memories/settings")
def memory_settings(req: MemorySettings, user: CurrentUser = Depends(get_current_user)):
    memory_store.set_memory_enabled(user.id, req.enabled)
    auth.audit("memory_settings", user, "memory", None, {"enabled": req.enabled})
    return {"enabled": req.enabled}


# ---------------------------------------------------------------------------
# 用户可选的模型与技能
# ---------------------------------------------------------------------------


@router.get("/models")
def model_options(user: CurrentUser = Depends(get_current_user)):
    from backend.llm_profiles import options_for_users

    return options_for_users(user.id)


@router.get("/skills")
def skill_options(user: CurrentUser = Depends(get_current_user)):
    from backend.assistant.skills import list_skills
    from backend.market import disabled_keys

    off = disabled_keys(user.id, "skill")
    return {"items": [{"name": s["name"], "title": s["title"], "description": s["description"]}
                      for s in list_skills(enabled_only=True) if s["name"] not in off]}
