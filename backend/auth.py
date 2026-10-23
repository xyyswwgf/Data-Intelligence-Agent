"""
认证与权限 — 用户、会话、角色鉴权、审计日志

- 密码：hashlib.scrypt（标准库，无额外依赖），带随机盐
- 会话：随机 token 只返回给客户端一次，数据库只存 SHA-256，可过期、可吊销
- 登录失败计数 + 临时锁定，防暴力破解
- 角色：viewer < editor < admin
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text

from backend.config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    AUTH_LOCK_MINUTES,
    AUTH_MAX_FAILED_LOGINS,
    AUTH_SESSION_TTL_HOURS,
)
from backend.database import get_connection

logger = logging.getLogger("nl2sql.auth")

ROLES = ("viewer", "editor", "admin")
_ROLE_RANK = {role: i for i, role in enumerate(ROLES)}
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


@dataclass
class CurrentUser:
    id: int
    username: str
    display_name: str
    role: str
    ip: str | None = None

    def has_role(self, role: str) -> bool:
        return _ROLE_RANK[self.role] >= _ROLE_RANK[role]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role}


# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${hash}".format(
        **_SCRYPT,
        salt=base64.b64encode(salt).decode(),
        hash=base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64), dklen=32, n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except Exception:
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    if password.isdigit() or password.isalpha():
        raise ValueError("密码需同时包含字母和数字")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


def audit(
    action: str,
    user: CurrentUser | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    detail: dict | None = None,
    ip: str | None = None,
    username: str | None = None,
) -> None:
    """写审计日志；审计失败只记日志，不影响业务操作。"""
    try:
        with get_connection() as conn:
            conn.execute(
                text("""
                    INSERT INTO audit_logs (user_id, username, action, target_type, target_id, detail, ip)
                    VALUES (:uid, :uname, :action, :ttype, :tid, CAST(:detail AS jsonb), :ip)
                """),
                {
                    "uid": user.id if user else None,
                    "uname": user.username if user else username,
                    "action": action,
                    "ttype": target_type,
                    "tid": None if target_id is None else str(target_id),
                    "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
                    "ip": ip or (user.ip if user else None),
                },
            )
    except Exception as exc:  # pragma: no cover - 审计失败不应影响主流程
        logger.error("写审计日志失败: %s", exc)


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------


def create_user(username: str, password: str, role: str, display_name: str | None = None) -> dict[str, Any]:
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("用户名不能为空且不超过 64 个字符")
    if role not in ROLES:
        raise ValueError(f"角色必须是 {', '.join(ROLES)} 之一")
    validate_password_strength(password)
    with get_connection() as conn:
        if conn.execute(text("SELECT 1 FROM users WHERE lower(username) = lower(:u)"), {"u": username}).first():
            raise ValueError(f"用户名 {username} 已存在")
        row = conn.execute(
            text("""
                INSERT INTO users (username, display_name, password_hash, role)
                VALUES (:u, :d, :h, :r)
                RETURNING id, username, display_name, role, is_active, created_at, last_login_at
            """),
            {"u": username, "d": (display_name or username).strip(), "h": hash_password(password), "r": role},
        ).mappings().one()
    return dict(row)


def list_users() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT id, username, display_name, role, is_active, created_at, last_login_at,
                   (locked_until IS NOT NULL AND locked_until > now()) AS locked
            FROM users ORDER BY id
        """)).mappings().all()
    return [dict(r) for r in rows]


def update_user(user_id: int, *, role: str | None = None, is_active: bool | None = None,
                display_name: str | None = None, password: str | None = None) -> dict[str, Any]:
    sets, params = [], {"id": user_id}
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"角色必须是 {', '.join(ROLES)} 之一")
        sets.append("role = :role")
        params["role"] = role
    if is_active is not None:
        sets.append("is_active = :active")
        params["active"] = is_active
    if display_name is not None:
        sets.append("display_name = :dn")
        params["dn"] = display_name.strip() or None
    if password is not None:
        validate_password_strength(password)
        sets += ["password_hash = :ph", "failed_logins = 0", "locked_until = NULL"]
        params["ph"] = hash_password(password)
    if not sets:
        raise ValueError("没有需要修改的字段")
    with get_connection() as conn:
        row = conn.execute(
            text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id "
                 "RETURNING id, username, display_name, role, is_active, created_at, last_login_at"),
            params,
        ).mappings().first()
        if not row:
            raise LookupError("用户不存在")
        # 停用或改密码后吊销已有会话
        if is_active is False or password is not None:
            conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :id"), {"id": user_id})
        # 不允许把最后一个启用的管理员降级或停用
        admins = conn.execute(text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active")).scalar()
        if admins == 0:
            raise ValueError("至少需要保留一个启用状态的管理员")
    return dict(row)


def bootstrap_admin() -> str | None:
    """users 表为空时创建初始管理员。返回生成的随机密码（仅在未配置 ADMIN_PASSWORD 时）。"""
    with get_connection() as conn:
        if conn.execute(text("SELECT EXISTS (SELECT 1 FROM users)")).scalar():
            return None
    password = ADMIN_PASSWORD or secrets.token_urlsafe(12) + "9a"
    create_user(ADMIN_USERNAME, password, "admin", "系统管理员")
    if ADMIN_PASSWORD:
        logger.info("已创建初始管理员 %s（密码来自 ADMIN_PASSWORD）", ADMIN_USERNAME)
        return None
    logger.warning("已创建初始管理员 %s，随机密码: %s —— 请登录后立即修改", ADMIN_USERNAME, password)
    return password


# ---------------------------------------------------------------------------
# 登录 / 会话
# ---------------------------------------------------------------------------


def login(username: str, password: str, ip: str | None, user_agent: str | None) -> dict[str, Any]:
    # 失败计数必须先提交再抛异常：在事务内抛异常会把计数一起回滚，锁定永远不会生效
    error: str | None = None
    with get_connection() as conn:
        user = conn.execute(
            text("SELECT * FROM users WHERE lower(username) = lower(:u)"), {"u": username.strip()}
        ).mappings().first()
        if user and user["locked_until"] and user["locked_until"] > _now():
            error = f"账号已被临时锁定，请 {AUTH_LOCK_MINUTES} 分钟后重试"
        elif not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
            error = "用户名或密码错误"
            if user:
                failed = user["failed_logins"] + 1
                locked = _now() + timedelta(minutes=AUTH_LOCK_MINUTES) if failed >= AUTH_MAX_FAILED_LOGINS else None
                conn.execute(
                    text("UPDATE users SET failed_logins = :f, locked_until = :l WHERE id = :id"),
                    {"f": 0 if locked else failed, "l": locked, "id": user["id"]},
                )
                if locked:
                    error = f"连续 {AUTH_MAX_FAILED_LOGINS} 次密码错误，账号已锁定 {AUTH_LOCK_MINUTES} 分钟"
    if error:
        raise PermissionError(error)

    with get_connection() as conn:
        token = secrets.token_urlsafe(32)
        expires_at = _now() + timedelta(hours=AUTH_SESSION_TTL_HOURS)
        conn.execute(
            text("""
                INSERT INTO auth_sessions (token_hash, user_id, expires_at, ip, user_agent)
                VALUES (:h, :uid, :exp, :ip, :ua)
            """),
            {"h": _token_hash(token), "uid": user["id"], "exp": expires_at, "ip": ip, "ua": (user_agent or "")[:300]},
        )
        conn.execute(
            text("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = :id"),
            {"id": user["id"]},
        )
        conn.execute(text("DELETE FROM auth_sessions WHERE expires_at < now()"))
    current = CurrentUser(user["id"], user["username"], user["display_name"], user["role"], ip)
    return {"token": token, "expires_at": expires_at.isoformat(), "user": current.public()}


def logout(token: str) -> None:
    with get_connection() as conn:
        conn.execute(text("DELETE FROM auth_sessions WHERE token_hash = :h"), {"h": _token_hash(token)})


def change_password(user: CurrentUser, old_password: str, new_password: str) -> None:
    with get_connection() as conn:
        stored = conn.execute(text("SELECT password_hash FROM users WHERE id = :id"), {"id": user.id}).scalar()
    if not stored or not verify_password(old_password, stored):
        raise PermissionError("原密码错误")
    update_user(user.id, password=new_password)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def get_current_user(request: Request) -> CurrentUser:
    token = _bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    with get_connection() as conn:
        row = conn.execute(
            text("""
                SELECT u.id, u.username, u.display_name, u.role, u.is_active, s.expires_at
                FROM auth_sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = :h
            """),
            {"h": _token_hash(token)},
        ).mappings().first()
        if not row or not row["is_active"] or row["expires_at"] < _now():
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
        conn.execute(text("UPDATE auth_sessions SET last_seen_at = now() WHERE token_hash = :h"), {"h": _token_hash(token)})
    return CurrentUser(row["id"], row["username"], row["display_name"], row["role"], _client_ip(request))


def require_role(role: str):
    """FastAPI 依赖：要求当前用户至少具备某个角色。"""

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has_role(role):
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return dependency


def list_audit_logs(limit: int = 50, offset: int = 0, action: str | None = None, username: str | None = None) -> dict:
    where, params = [], {"limit": min(max(limit, 1), 200), "offset": max(offset, 0)}
    if action:
        where.append("action = :action")
        params["action"] = action
    if username:
        where.append("username ILIKE :uname")
        params["uname"] = f"%{username}%"
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with get_connection() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM audit_logs {clause}"), params).scalar()
        rows = conn.execute(
            text(f"""
                SELECT id, username, action, target_type, target_id, detail, ip, created_at
                FROM audit_logs {clause} ORDER BY id DESC LIMIT :limit OFFSET :offset
            """),
            params,
        ).mappings().all()
    return {"total": total, "items": [dict(r) for r in rows]}
