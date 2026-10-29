"""
方案、订阅周期、余额与扣费

订阅按月滚动：period_start ~ period_end 为当期，当期已用额度记在 used_credits，进入下一期自动清零。
expires_at 为空表示长期有效；到期后自动回到默认方案（通常是免费版）。
"""

from __future__ import annotations

import calendar
import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.config import BILLING_DEFAULT_PRICE_INPUT, BILLING_DEFAULT_PRICE_OUTPUT, BILLING_ENFORCE
from backend.database import get_connection

_PLAN_FIELDS = ("code", "name", "description", "price_cents", "monthly_credits", "features", "enabled", "sort_order")


class QuotaExceeded(Exception):
    """当期方案额度和余额都已用完。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def add_months(value: datetime, months: int) -> datetime:
    month = value.month - 1 + months
    year, month = value.year + month // 12, month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _num(value: Any) -> float:
    return float(value or 0)


# ---------------------------------------------------------------------------
# 方案
# ---------------------------------------------------------------------------


def _plan(row) -> dict[str, Any]:
    data = dict(row)
    data["monthly_credits"] = _num(data["monthly_credits"])
    return data


def list_plans(include_disabled: bool = False) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT p.*, (SELECT COUNT(*) FROM user_subscriptions s WHERE s.plan_id = p.id) AS subscribers
            FROM billing_plans p {"" if include_disabled else "WHERE enabled"} ORDER BY sort_order, id
        """)).mappings().all()
    return [_plan(r) for r in rows]


def _validate_plan(data: dict) -> None:
    code = data.get("code")
    if code is not None and not (code.replace("_", "").isalnum() and code.isascii() and 1 <= len(code) <= 32):
        raise ValueError("方案标识只能包含字母、数字和下划线")
    if data.get("price_cents") is not None and data["price_cents"] < 0:
        raise ValueError("价格不能为负数")
    if data.get("monthly_credits") is not None and data["monthly_credits"] < 0:
        raise ValueError("每月额度不能为负数")


def create_plan(data: dict) -> dict[str, Any]:
    _validate_plan(data)
    params = {k: data.get(k) for k in _PLAN_FIELDS}
    params["features"] = json.dumps(params["features"] or [], ensure_ascii=False)
    params["enabled"] = params["enabled"] is not False
    params["sort_order"] = params["sort_order"] or 0
    params["description"] = params["description"] or ""
    with get_connection() as conn:
        if conn.execute(text("SELECT 1 FROM billing_plans WHERE code = :c"), {"c": params["code"]}).first():
            raise ValueError(f"方案标识 {params['code']} 已存在")
        row = conn.execute(text("""
            INSERT INTO billing_plans (code, name, description, price_cents, monthly_credits, features, enabled, sort_order)
            VALUES (:code, :name, :description, :price_cents, :monthly_credits, CAST(:features AS jsonb), :enabled, :sort_order)
            RETURNING *
        """), params).mappings().one()
    return _plan(row)


def update_plan(plan_id: int, data: dict) -> dict[str, Any]:
    _validate_plan(data)
    sets, params = [], {"id": plan_id}
    for key in _PLAN_FIELDS:
        if key in data and data[key] is not None and key != "code":
            value = json.dumps(data[key], ensure_ascii=False) if key == "features" else data[key]
            sets.append(f"{key} = CAST(:{key} AS jsonb)" if key == "features" else f"{key} = :{key}")
            params[key] = value
    with get_connection() as conn:
        plan = conn.execute(text("SELECT * FROM billing_plans WHERE id = :id"), {"id": plan_id}).mappings().first()
        if not plan:
            raise LookupError("方案不存在")
        if plan["is_default"] and data.get("enabled") is False:
            raise ValueError("默认方案不能停用，请先把其他方案设为默认")
        if data.get("is_default"):
            conn.execute(text("UPDATE billing_plans SET is_default = FALSE WHERE is_default"))
            sets.append("is_default = TRUE")
        if sets:
            conn.execute(text(f"UPDATE billing_plans SET {', '.join(sets)}, updated_at = now() WHERE id = :id"), params)
        row = conn.execute(text("SELECT * FROM billing_plans WHERE id = :id"), {"id": plan_id}).mappings().one()
    return _plan(row)


def _default_plan(conn: Connection):
    plan = conn.execute(text("SELECT * FROM billing_plans WHERE is_default")).mappings().first()
    if plan is None:  # 管理员误删了所有方案时的兜底：没有额度
        plan = conn.execute(text("""
            INSERT INTO billing_plans (code, name, monthly_credits, is_default) VALUES ('free', '免费版', 0, TRUE)
            ON CONFLICT (code) DO UPDATE SET is_default = TRUE RETURNING *
        """)).mappings().one()
    return plan


# ---------------------------------------------------------------------------
# 订阅与余额
# ---------------------------------------------------------------------------


def _subscription(conn: Connection, user_id: int, lock: bool = False):
    """取出用户的订阅（锁行），按需创建、续到当期、处理到期。"""
    now = _now()
    sub = conn.execute(text(f"SELECT * FROM user_subscriptions WHERE user_id = :u {'FOR UPDATE' if lock else ''}"),
                       {"u": user_id}).mappings().first()
    if sub is None:
        plan = _default_plan(conn)
        conn.execute(text("""
            INSERT INTO user_subscriptions (user_id, plan_id, period_start, period_end)
            VALUES (:u, :p, :s, :e) ON CONFLICT (user_id) DO NOTHING
        """), {"u": user_id, "p": plan["id"], "s": now, "e": add_months(now, 1)})
        sub = conn.execute(text(f"SELECT * FROM user_subscriptions WHERE user_id = :u {'FOR UPDATE' if lock else ''}"),
                           {"u": user_id}).mappings().one()
    sub = dict(sub)
    changed = False
    if sub["expires_at"] and now >= sub["expires_at"]:
        sub.update(plan_id=_default_plan(conn)["id"], period_start=now, period_end=add_months(now, 1),
                   expires_at=None, used_credits=Decimal(0))
        changed = True
    while now >= sub["period_end"]:
        sub["period_start"], sub["period_end"] = sub["period_end"], add_months(sub["period_end"], 1)
        sub["used_credits"] = Decimal(0)
        changed = True
    if changed:
        conn.execute(text("""
            UPDATE user_subscriptions SET plan_id = :plan_id, period_start = :period_start, period_end = :period_end,
                expires_at = :expires_at, used_credits = :used_credits, updated_at = now()
            WHERE user_id = :user_id
        """), sub)
    sub["plan"] = conn.execute(text("SELECT * FROM billing_plans WHERE id = :p"), {"p": sub["plan_id"]}).mappings().one()
    return sub


def _balance(conn: Connection, user_id: int, lock: bool = False) -> Decimal:
    conn.execute(text("INSERT INTO user_wallets (user_id) VALUES (:u) ON CONFLICT (user_id) DO NOTHING"), {"u": user_id})
    return conn.execute(text(f"SELECT balance FROM user_wallets WHERE user_id = :u {'FOR UPDATE' if lock else ''}"),
                        {"u": user_id}).scalar_one()


def account_summary(user_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        sub = _subscription(conn, user_id)
        balance = _balance(conn, user_id)
    plan = sub["plan"]
    quota, used = _num(plan["monthly_credits"]), _num(sub["used_credits"])
    remaining = max(quota - used, 0)
    return {
        "plan": {k: plan[k] for k in ("id", "code", "name", "description", "price_cents", "features")}
        | {"monthly_credits": quota},
        "period_start": sub["period_start"], "period_end": sub["period_end"], "expires_at": sub["expires_at"],
        "plan_used": used, "plan_remaining": remaining, "balance": _num(balance),
        "available": remaining + max(_num(balance), 0),
        "enforce": BILLING_ENFORCE,
    }


def ensure_can_use(user_id: int, role: str) -> None:
    """提问前检查：方案额度和余额都用完时拒绝。管理员、或关闭了 BILLING_ENFORCE 时不拦截。"""
    if not BILLING_ENFORCE or role == "admin":
        return
    summary = account_summary(user_id)
    if summary["available"] <= 0:
        raise QuotaExceeded(f"本期「{summary['plan']['name']}」额度已用完，余额不足。请联系管理员升级方案或充值。")


# ---------------------------------------------------------------------------
# 扣费
# ---------------------------------------------------------------------------


def _prices(conn: Connection, profile_id: int | None) -> tuple[Decimal, Decimal]:
    row = None
    if profile_id:
        row = conn.execute(text("SELECT price_input, price_output FROM llm_profiles WHERE id = :id"),
                           {"id": profile_id}).first()
    price_in = row[0] if row and row[0] is not None else Decimal(str(BILLING_DEFAULT_PRICE_INPUT))
    price_out = row[1] if row and row[1] is not None else Decimal(str(BILLING_DEFAULT_PRICE_OUTPUT))
    return Decimal(price_in), Decimal(price_out)


def charge(user_id: int, session_id: str | None, mode: str | None, profile_id: int | None, model: str,
           input_tokens: int, output_tokens: int) -> dict[str, Any]:
    """记录一次模型调用并扣费：先扣当期方案额度，不足部分扣余额（余额允许因最后一次调用略为负数）。"""
    with get_connection() as conn:
        price_in, price_out = _prices(conn, profile_id)
        credits = (Decimal(input_tokens) / 1000 * price_in + Decimal(output_tokens) / 1000 * price_out) \
            .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        sub = _subscription(conn, user_id, lock=True)
        remaining = max(Decimal(sub["plan"]["monthly_credits"]) - Decimal(sub["used_credits"]), Decimal(0))
        from_plan = min(credits, remaining)
        from_balance = credits - from_plan
        if from_plan:
            conn.execute(text("UPDATE user_subscriptions SET used_credits = used_credits + :c, updated_at = now() "
                              "WHERE user_id = :u"), {"c": from_plan, "u": user_id})
        if from_balance:
            _balance(conn, user_id, lock=True)
            conn.execute(text("UPDATE user_wallets SET balance = balance - :c, updated_at = now() WHERE user_id = :u"),
                         {"c": from_balance, "u": user_id})
        conn.execute(text("""
            INSERT INTO usage_records (user_id, session_id, mode, profile_id, model, input_tokens, output_tokens,
                                       credits, plan_credits, balance_credits)
            VALUES (:u, :s, :m, :p, :model, :i, :o, :c, :cp, :cb)
        """), {"u": user_id, "s": session_id, "m": mode, "p": profile_id, "model": model, "i": input_tokens,
               "o": output_tokens, "c": credits, "cp": from_plan, "cb": from_balance})
    return {"credits": float(credits), "plan_credits": float(from_plan), "balance_credits": float(from_balance)}


# ---------------------------------------------------------------------------
# 使用量与账单查询
# ---------------------------------------------------------------------------


def usage_report(user_id: int | None, days: int = 30) -> dict[str, Any]:
    """user_id 为空时统计全体用户（管理员视图）。"""
    days = max(1, min(days, 365))
    who = "AND user_id = :u" if user_id else ""
    params = {"u": user_id, "d": days}
    with get_connection() as conn:
        daily = conn.execute(text(f"""
            SELECT d::date AS day, COALESCE(SUM(r.credits), 0) AS credits, COUNT(r.id) AS requests,
                   COALESCE(SUM(r.input_tokens), 0) AS input_tokens, COALESCE(SUM(r.output_tokens), 0) AS output_tokens
            FROM generate_series(current_date - (:d - 1), current_date, interval '1 day') AS d
            LEFT JOIN usage_records r ON r.created_at::date = d::date {who.replace("user_id", "r.user_id")}
            GROUP BY d ORDER BY d
        """), params).mappings().all()
        by_model = conn.execute(text(f"""
            SELECT COALESCE(p.name, r.model, '未知') AS name, COUNT(*) AS requests, SUM(r.credits) AS credits,
                   SUM(r.input_tokens) AS input_tokens, SUM(r.output_tokens) AS output_tokens
            FROM usage_records r LEFT JOIN llm_profiles p ON p.id = r.profile_id
            WHERE r.created_at >= current_date - (:d - 1) {who.replace("user_id", "r.user_id")}
            GROUP BY 1 ORDER BY credits DESC
        """), params).mappings().all()
        by_mode = conn.execute(text(f"""
            SELECT COALESCE(mode, 'other') AS mode, COUNT(*) AS requests, SUM(credits) AS credits
            FROM usage_records WHERE created_at >= current_date - (:d - 1) {who}
            GROUP BY 1 ORDER BY credits DESC
        """), params).mappings().all()
        recent = conn.execute(text(f"""
            SELECT r.created_at, r.mode, COALESCE(p.name, r.model) AS model, r.input_tokens, r.output_tokens,
                   r.credits, r.plan_credits, r.balance_credits, u.username
            FROM usage_records r LEFT JOIN llm_profiles p ON p.id = r.profile_id JOIN users u ON u.id = r.user_id
            WHERE TRUE {who.replace("user_id", "r.user_id")} ORDER BY r.created_at DESC LIMIT 30
        """), params).mappings().all()
        top_users = [] if user_id else conn.execute(text("""
            SELECT u.username, u.display_name, COUNT(*) AS requests, SUM(r.credits) AS credits
            FROM usage_records r JOIN users u ON u.id = r.user_id
            WHERE r.created_at >= current_date - (:d - 1) GROUP BY 1, 2 ORDER BY credits DESC LIMIT 10
        """), params).mappings().all()

    def rows(items):
        return [{k: (_num(v) if isinstance(v, Decimal) else v) for k, v in dict(r).items()} for r in items]

    daily_rows = rows(daily)
    return {
        "days": days,
        "daily": daily_rows,
        "totals": {k: sum(_num(r[k]) for r in daily_rows) for k in ("credits", "requests", "input_tokens", "output_tokens")},
        "by_model": rows(by_model),
        "by_mode": rows(by_mode),
        "recent": rows(recent),
        "top_users": rows(top_users),
    }


def list_transactions(user_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT t.*, p.name AS plan_name, u.username, o.username AS operator
            FROM billing_transactions t
            LEFT JOIN billing_plans p ON p.id = t.plan_id
            JOIN users u ON u.id = t.user_id
            LEFT JOIN users o ON o.id = t.operator_id
            {"WHERE t.user_id = :u" if user_id else ""}
            ORDER BY t.created_at DESC LIMIT :l
        """), {"u": user_id, "l": max(1, min(limit, 500))}).mappings().all()
    return [{**dict(r), "credits": _num(r["credits"])} for r in rows]


# ---------------------------------------------------------------------------
# 管理员操作：开通方案、充值 / 调整
# ---------------------------------------------------------------------------


def list_accounts() -> list[dict[str, Any]]:
    with get_connection() as conn:
        user_ids = conn.execute(text("SELECT id FROM users ORDER BY id")).scalars().all()
        for uid in user_ids:  # 确保每个用户都有当期订阅和钱包（新用户首次出现在列表时创建）
            _subscription(conn, uid)
            _balance(conn, uid)
        rows = conn.execute(text("""
            SELECT u.id AS user_id, u.username, u.display_name, u.role, u.is_active,
                   p.id AS plan_id, p.name AS plan_name, p.monthly_credits, s.used_credits, s.period_end, s.expires_at,
                   w.balance,
                   (SELECT COALESCE(SUM(credits), 0) FROM usage_records r
                    WHERE r.user_id = u.id AND r.created_at >= now() - interval '30 days') AS credits_30d
            FROM users u
            JOIN user_subscriptions s ON s.user_id = u.id
            JOIN billing_plans p ON p.id = s.plan_id
            JOIN user_wallets w ON w.user_id = u.id
            ORDER BY u.id
        """)).mappings().all()
    return [{k: (_num(v) if isinstance(v, Decimal) else v) for k, v in dict(r).items()} for r in rows]


def set_subscription(user_id: int, plan_id: int, months: int, amount_cents: int, note: str | None,
                     operator_id: int | None) -> dict[str, Any]:
    """为用户开通 / 更换方案：从现在起算新周期，额度清零重算。months=0 表示长期有效。"""
    if months < 0 or months > 120:
        raise ValueError("开通时长为 0–120 个月（0 表示长期有效）")
    now = _now()
    with get_connection() as conn:
        plan = conn.execute(text("SELECT * FROM billing_plans WHERE id = :p AND enabled"), {"p": plan_id}).mappings().first()
        if not plan:
            raise LookupError("方案不存在或已停用")
        if not conn.execute(text("SELECT 1 FROM users WHERE id = :u"), {"u": user_id}).first():
            raise LookupError("用户不存在")
        _subscription(conn, user_id, lock=True)
        conn.execute(text("""
            UPDATE user_subscriptions SET plan_id = :p, period_start = :s, period_end = :e, expires_at = :x,
                used_credits = 0, updated_at = now()
            WHERE user_id = :u
        """), {"u": user_id, "p": plan_id, "s": now, "e": add_months(now, 1), "x": add_months(now, months) if months else None})
        conn.execute(text("""
            INSERT INTO billing_transactions (user_id, kind, plan_id, credits, amount_cents, note, operator_id)
            VALUES (:u, 'subscribe', :p, :c, :a, :n, :o)
        """), {"u": user_id, "p": plan_id, "c": plan["monthly_credits"], "a": max(amount_cents, 0),
               "n": note or (f"开通「{plan['name']}」{months} 个月" if months else f"开通「{plan['name']}」长期有效"),
               "o": operator_id})
    return account_summary(user_id)


def adjust_balance(user_id: int, credits: float, amount_cents: int, note: str | None, operator_id: int | None) -> dict[str, Any]:
    """充值（正数）或扣减（负数）余额点数，记入账单流水。"""
    if credits == 0:
        raise ValueError("点数不能为 0")
    if abs(credits) > 100_000_000:
        raise ValueError("单次调整的点数过大")
    with get_connection() as conn:
        if not conn.execute(text("SELECT 1 FROM users WHERE id = :u"), {"u": user_id}).first():
            raise LookupError("用户不存在")
        _balance(conn, user_id, lock=True)
        conn.execute(text("UPDATE user_wallets SET balance = balance + :c, updated_at = now() WHERE user_id = :u"),
                     {"c": Decimal(str(credits)), "u": user_id})
        conn.execute(text("""
            INSERT INTO billing_transactions (user_id, kind, credits, amount_cents, note, operator_id)
            VALUES (:u, :k, :c, :a, :n, :o)
        """), {"u": user_id, "k": "recharge" if credits > 0 else "adjust", "c": Decimal(str(credits)),
               "a": max(amount_cents, 0), "n": note, "o": operator_id})
    return account_summary(user_id)
