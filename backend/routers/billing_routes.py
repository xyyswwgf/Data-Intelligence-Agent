"""使用量、账单、方案与「发现」市场 API。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend import auth, market
from backend.auth import CurrentUser, get_current_user, require_role
from backend.billing import accounts, payments

router = APIRouter(prefix="/api", tags=["billing"])
Admin = Depends(require_role("admin"))


def _errors(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class PlanIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=50)
    description: str = Field("", max_length=300)
    price_cents: int = Field(0, ge=0, le=100_000_000)
    monthly_credits: float = Field(0, ge=0, le=1_000_000_000)
    features: list[str] = Field(default_factory=list, max_length=12)
    enabled: bool = True
    sort_order: int = 0


class PlanPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=300)
    price_cents: int | None = Field(None, ge=0, le=100_000_000)
    monthly_credits: float | None = Field(None, ge=0, le=1_000_000_000)
    features: list[str] | None = Field(None, max_length=12)
    enabled: bool | None = None
    sort_order: int | None = None
    is_default: bool | None = None


class SubscriptionIn(BaseModel):
    plan_id: int
    months: int = Field(1, ge=0, le=120, description="0 表示长期有效")
    amount_cents: int = Field(0, ge=0, le=100_000_000, description="实收金额（分），仅用于记账")
    note: str | None = Field(None, max_length=200)


class BalanceIn(BaseModel):
    credits: float = Field(..., description="正数充值，负数扣减")
    amount_cents: int = Field(0, ge=0, le=100_000_000)
    note: str | None = Field(None, max_length=200)


class MarketPref(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# 当前用户：方案、使用量、账单
# ---------------------------------------------------------------------------


@router.get("/billing/me")
def my_account(user: CurrentUser = Depends(get_current_user)):
    return accounts.account_summary(user.id)


@router.get("/billing/usage")
def my_usage(days: int = Query(30, ge=1, le=365), user: CurrentUser = Depends(get_current_user)):
    return accounts.usage_report(user.id, days)


@router.get("/billing/transactions")
def my_transactions(user: CurrentUser = Depends(get_current_user)):
    return {"items": accounts.list_transactions(user.id)}


@router.get("/billing/plans")
def public_plans(user: CurrentUser = Depends(get_current_user)):
    return {"items": accounts.list_plans(), "checkout": payments.checkout_options()}


# ---------------------------------------------------------------------------
# 管理员：方案与账户
# ---------------------------------------------------------------------------


@router.get("/admin/billing/plans")
def admin_plans(user: CurrentUser = Admin):
    return {"items": accounts.list_plans(include_disabled=True)}


@router.post("/admin/billing/plans", status_code=201)
def admin_create_plan(req: PlanIn, user: CurrentUser = Admin):
    plan = _errors(accounts.create_plan, req.model_dump())
    auth.audit("plan_create", user, "billing_plan", str(plan["id"]), {"code": plan["code"]})
    return plan


@router.patch("/admin/billing/plans/{plan_id}")
def admin_update_plan(plan_id: int, req: PlanPatch, user: CurrentUser = Admin):
    data = req.model_dump(exclude_unset=True)
    plan = _errors(accounts.update_plan, plan_id, data)
    auth.audit("plan_update", user, "billing_plan", str(plan_id), {"fields": sorted(data)})
    return plan


@router.get("/admin/billing/accounts")
def admin_accounts(user: CurrentUser = Admin):
    return {"items": accounts.list_accounts()}


@router.post("/admin/billing/accounts/{user_id}/subscription")
def admin_set_subscription(user_id: int, req: SubscriptionIn, user: CurrentUser = Admin):
    result = _errors(accounts.set_subscription, user_id, req.plan_id, req.months, req.amount_cents, req.note, user.id)
    auth.audit("plan_assign", user, "user", str(user_id), {"plan_id": req.plan_id, "months": req.months,
                                                         "amount_cents": req.amount_cents})
    return result


@router.post("/admin/billing/accounts/{user_id}/balance")
def admin_adjust_balance(user_id: int, req: BalanceIn, user: CurrentUser = Admin):
    result = _errors(accounts.adjust_balance, user_id, req.credits, req.amount_cents, req.note, user.id)
    auth.audit("balance_adjust", user, "user", str(user_id), {"credits": req.credits, "amount_cents": req.amount_cents})
    return result


@router.get("/admin/billing/usage")
def admin_usage(days: int = Query(30, ge=1, le=365), user: CurrentUser = Admin):
    return accounts.usage_report(None, days)


@router.get("/admin/billing/transactions")
def admin_transactions(user_id: int | None = None, user: CurrentUser = Admin):
    return {"items": accounts.list_transactions(user_id, limit=300)}


# ---------------------------------------------------------------------------
# 发现：模型、技能、工具目录
# ---------------------------------------------------------------------------


@router.get("/market")
def market_catalog(user: CurrentUser = Depends(get_current_user)):
    return market.catalog(user.id, user.role)


@router.put("/market/{kind}/{key}")
def market_toggle(kind: Literal["model", "skill", "tool"], key: str, req: MarketPref,
                  user: CurrentUser = Depends(get_current_user)):
    return _errors(market.set_enabled, user.id, user.role, kind, key, req.enabled)
