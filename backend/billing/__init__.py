"""
使用量与计费

  - 计量（metering）：每次调用大模型按 token 数和模型单价折算成「点数」，写入 usage_records
  - 扣费（accounts）：先扣当期方案额度，用完再扣余额；额度和余额都用完时拦截新的提问
  - 账单（billing_transactions）：开通方案、充值、调整的流水
  - 支付（payments）：预留微信 / 支付宝接口，目前由管理员手动开通和充值
"""
