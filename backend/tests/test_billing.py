"""使用量计量、方案 + 余额扣费、账单、额度拦截，以及「发现」中的启用 / 停用。"""

import json
import uuid

import pytest
from sqlalchemy import text

from backend.llm_gateway import LLMResponse, Provider, set_provider
from backend.tests.conftest import requires_db

pytestmark = requires_db


class TokenProvider(Provider):
    """每次调用固定消耗 1000 输入 + 500 输出 token（默认价下 = 1 + 1 = 2 点）。"""

    name = "tokens"
    model = "fake-tokens"

    def configured(self):
        return True

    def chat(self, system, messages, tools=None, max_tokens=1000, temperature=0.2, thinking=False):
        return LLMResponse(text="好的，这是回答。", input_tokens=1000, output_tokens=500)


@pytest.fixture(autouse=True)
def _provider():
    set_provider(TokenProvider())
    yield
    set_provider(None)


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).json()


def _ask(client, headers, mode="kb"):
    sid = client.post("/api/chat/sessions", headers=headers).json()["id"]
    return client.post(f"/api/chat/sessions/{sid}/messages", headers=headers, json={"content": "店长店提怎么算？", "mode": mode})


def _plan(client, admin_headers, credits):
    code = f"t{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/admin/billing/plans", headers=admin_headers,
                       json={"code": code, "name": f"测试{code}", "monthly_credits": credits, "price_cents": 100})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_usage_is_metered_against_plan(client, make_user):
    headers = make_user("viewer")
    before = client.get("/api/billing/me", headers=headers).json()
    assert before["plan"]["code"] == "free" and before["plan_used"] == 0

    assert _ask(client, headers).status_code == 200
    after = client.get("/api/billing/me", headers=headers).json()
    assert after["plan_used"] > 0 and after["balance"] == 0  # 先扣方案额度

    usage = client.get("/api/billing/usage?days=7", headers=headers).json()
    assert usage["totals"]["requests"] >= 1 and usage["totals"]["input_tokens"] >= 1000
    assert usage["recent"][0]["model"] == "fake-tokens" and usage["recent"][0]["mode"] == "kb"
    assert len(usage["daily"]) == 7


def test_plan_then_balance_then_blocked(client, admin_headers, make_user):
    headers = make_user("viewer")
    uid = _me(client, headers)["id"]
    plan = _plan(client, admin_headers, 1)  # 每月只有 1 点
    assert client.post(f"/api/admin/billing/accounts/{uid}/subscription", headers=admin_headers,
                       json={"plan_id": plan["id"], "months": 1, "amount_cents": 100}).status_code == 200

    from backend.billing.accounts import account_summary, charge

    result = charge(uid, None, "kb", None, "m", 1000, 500)  # 2 点：1 点来自方案，1 点来自余额
    assert result == {"credits": 2.0, "plan_credits": 1.0, "balance_credits": 1.0}
    summary = account_summary(uid)
    assert summary["plan_remaining"] == 0 and summary["balance"] == -1

    blocked = _ask(client, headers)
    assert blocked.status_code == 402 and "额度已用完" in blocked.json()["detail"]

    assert client.post(f"/api/admin/billing/accounts/{uid}/balance", headers=admin_headers,
                       json={"credits": 10, "amount_cents": 1000, "note": "测试充值"}).status_code == 200
    assert _ask(client, headers).status_code == 200
    assert client.get("/api/billing/me", headers=headers).json()["balance"] < 9

    kinds = [t["kind"] for t in client.get("/api/billing/transactions", headers=headers).json()["items"]]
    assert kinds[:2] == ["recharge", "subscribe"]


def test_admin_is_not_blocked(client, admin_headers):
    uid = _me(client, admin_headers)["id"]
    from backend.billing.accounts import ensure_can_use
    from backend.database import get_connection

    with get_connection() as conn:
        conn.execute(text("UPDATE user_wallets SET balance = -100 WHERE user_id = :u"), {"u": uid})
    try:
        ensure_can_use(uid, "admin")
    finally:
        with get_connection() as conn:
            conn.execute(text("UPDATE user_wallets SET balance = 0 WHERE user_id = :u"), {"u": uid})


def test_period_rolls_and_expired_plan_falls_back(client, admin_headers, make_user):
    headers = make_user("viewer")
    uid = _me(client, headers)["id"]
    plan = _plan(client, admin_headers, 500)
    client.post(f"/api/admin/billing/accounts/{uid}/subscription", headers=admin_headers,
                json={"plan_id": plan["id"], "months": 3})
    from backend.billing.accounts import account_summary
    from backend.database import get_connection

    with get_connection() as conn:
        conn.execute(text("""UPDATE user_subscriptions SET used_credits = 120,
            period_start = now() - interval '40 days', period_end = now() - interval '10 days' WHERE user_id = :u"""),
                     {"u": uid})
    rolled = account_summary(uid)
    assert rolled["plan"]["id"] == plan["id"] and rolled["plan_used"] == 0  # 进入新周期，额度清零

    with get_connection() as conn:
        conn.execute(text("UPDATE user_subscriptions SET expires_at = now() - interval '1 day' WHERE user_id = :u"), {"u": uid})
    assert account_summary(uid)["plan"]["code"] == "free"  # 到期回到默认方案


def test_billing_admin_requires_admin(client, make_user):
    viewer = make_user("viewer")
    assert client.get("/api/admin/billing/accounts", headers=viewer).status_code == 403
    assert client.post("/api/admin/billing/plans", headers=viewer, json={"code": "x", "name": "x"}).status_code == 403
    plans = client.get("/api/billing/plans", headers=viewer).json()
    assert {"free", "pro", "team"} <= {p["code"] for p in plans["items"]} and plans["checkout"]["available"] is False


def test_admin_overview(client, admin_headers):
    accounts = client.get("/api/admin/billing/accounts", headers=admin_headers).json()["items"]
    assert accounts and {"plan_name", "balance", "credits_30d"} <= set(accounts[0])
    usage = client.get("/api/admin/billing/usage?days=30", headers=admin_headers).json()
    assert "top_users" in usage and "by_model" in usage


def test_market_toggles(client, admin_headers, make_user):
    headers = make_user("editor")
    catalog = client.get("/api/market", headers=headers).json()
    assert catalog["external"]["connected"] is False
    skill = catalog["skills"][0]
    builtin = next(t for t in catalog["tools"] if t["locked"])

    off = client.put(f"/api/market/skill/{skill['key']}", headers=headers, json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert skill["key"] not in {s["name"] for s in client.get("/api/skills", headers=headers).json()["items"]}
    # 其他用户不受影响
    other = make_user("viewer")
    assert skill["key"] in {s["name"] for s in client.get("/api/skills", headers=other).json()["items"]}

    assert client.put(f"/api/market/tool/{builtin['key']}", headers=headers, json={"enabled": False}).status_code == 400
    assert client.put("/api/market/skill/no_such_skill", headers=headers, json={"enabled": False}).status_code == 404


def test_privacy_export_and_delete(client, make_user):
    headers = make_user("viewer")
    assert _ask(client, headers).status_code == 200
    exported = client.get("/api/me/export", headers=headers)
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    data = json.loads(exported.content)
    assert data["sessions"] and data["usage"]["totals"]["requests"] >= 1

    deleted = client.delete("/api/chat/sessions", headers=headers).json()
    assert deleted["deleted"] >= 1
    assert client.get("/api/chat/sessions", headers=headers).json()["items"] == []
