"""认证与用户管理 API。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend import auth
from backend.auth import CurrentUser, get_current_user, require_role

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=200)
    role: Literal["viewer", "editor", "admin"] = "viewer"
    display_name: str | None = Field(None, max_length=100)


class UpdateUserRequest(BaseModel):
    role: Literal["viewer", "editor", "admin"] | None = None
    is_active: bool | None = None
    display_name: str | None = Field(None, max_length=100)
    password: str | None = Field(None, min_length=8, max_length=200)


@router.post("/auth/login")
def login(req: LoginRequest, request: Request):
    ip = auth._client_ip(request)
    try:
        result = auth.login(req.username, req.password, ip, request.headers.get("user-agent"))
    except PermissionError as exc:
        auth.audit("login_failed", username=req.username[:64], ip=ip)
        raise HTTPException(status_code=401, detail=str(exc))
    auth.audit("login", CurrentUser(**result["user"], ip=ip))
    return result


@router.post("/auth/logout")
def logout(request: Request, user: CurrentUser = Depends(get_current_user)):
    auth.logout(auth._bearer_token(request) or "")
    auth.audit("logout", user)
    return {"ok": True}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return user.public()


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest, user: CurrentUser = Depends(get_current_user)):
    try:
        auth.change_password(user, req.old_password, req.new_password)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auth.audit("change_password", user, "user", user.id)
    return {"ok": True, "message": "密码已修改，请重新登录"}


@router.get("/admin/users")
def list_users(user: CurrentUser = Depends(require_role("admin"))):
    return {"items": auth.list_users()}


@router.post("/admin/users", status_code=201)
def create_user(req: CreateUserRequest, user: CurrentUser = Depends(require_role("admin"))):
    try:
        created = auth.create_user(req.username, req.password, req.role, req.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auth.audit("user_create", user, "user", created["id"], {"username": created["username"], "role": created["role"]})
    return created


@router.patch("/admin/users/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, user: CurrentUser = Depends(require_role("admin"))):
    if user_id == user.id and (req.is_active is False or (req.role and req.role != "admin")):
        raise HTTPException(status_code=400, detail="不能停用或降级自己的管理员账号")
    try:
        updated = auth.update_user(user_id, role=req.role, is_active=req.is_active,
                                   display_name=req.display_name, password=req.password)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    changes = req.model_dump(exclude_none=True)
    if "password" in changes:
        changes["password"] = "******"
    auth.audit("user_update", user, "user", user_id, changes)
    return updated


@router.get("/admin/audit")
def audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = None,
    username: str | None = None,
    user: CurrentUser = Depends(require_role("admin")),
):
    return auth.list_audit_logs(limit, offset, action, username)
