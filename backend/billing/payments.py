"""
在线支付（预留）

目前由管理员在「设置 → 方案与计费」中手动开通方案和充值。接入微信支付 / 支付宝时：
  1. 实现 PaymentChannel（下单返回支付二维码或跳转地址；校验回调签名）
  2. 在 CHANNELS 中注册，并把商户号、密钥配置到环境变量
  3. 回调验签成功后，把对应的 billing_transactions 记录从 pending 改为 paid，
     再调用 accounts.set_subscription / adjust_balance 完成开通或入账（operator_id 为空表示用户自助支付）
对外收费前需要先完成生成式 AI 服务备案和相关经营许可。
"""

from __future__ import annotations

from typing import Any, Protocol


class PaymentChannel(Protocol):
    code: str  # wechat | alipay

    def create_order(self, user_id: int, amount_cents: int, subject: str, out_trade_no: str) -> dict[str, Any]:
        """返回 {"qr_code": ...} 或 {"pay_url": ...}"""

    def verify_callback(self, headers: dict, body: bytes) -> dict[str, Any] | None:
        """验签通过返回 {"out_trade_no", "amount_cents", "external_no"}，否则 None"""


CHANNELS: dict[str, PaymentChannel] = {}


def checkout_options() -> dict[str, Any]:
    if CHANNELS:
        return {"available": True, "channels": list(CHANNELS)}
    return {"available": False, "channels": [],
            "message": "在线支付尚未开通。请联系管理员为你开通方案或充值点数。"}
