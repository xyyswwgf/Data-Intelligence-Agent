"""
虚构的门店样例数据 — 用于自动化测试和没有真实数据时的演示，结构与真实台账一致，数值和人名均为虚构。

  python -m backend.store_data.sample      # 写入当前数据库（会清空门店业务表）

数据是确定性的（固定随机种子），测试可以对结果做精确断言：
  - 2026-07、2026-08 两个月，四家门店，每店两名店员
  - 8 月有门店 / 店员毛利目标和产品线目标；南锣达成最高、乐堤港最低
  - 库龄表中每店都有超过 90 天的商品
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.store_data.parsers import PHONE_TYPES, is_phone
from backend.store_data.schema import BUSINESS_TABLES, STORES

STAFF = {
    "wangjing": ["王一", "王二"],
    "nanluo": ["南一", "南二"],
    "letigang": ["乐一", "乐二"],
    "yangguang": ["阳一", "阳二"],
}
# (类型, 系列, 商品, 售价, 进店价)
PRODUCTS = [
    ("FD", "V6", "荣耀Magic V6-12+512G全网通5G绒黑色", 9999, 9200),
    ("数字", "600Pro", "荣耀600 Pro-12+512G全网通5G幸运星", 4099, 3784),
    ("X系列", "X80PM", "荣耀X80 Pro Max-8+256G全网通5G元气橙", 2099, 1935),
    ("Power", "Power2", "荣耀Power2-12+512G全网通5G幻夜黑", 2999, 2640),
    ("全场景", "穿戴", "荣耀手表6 Plus 标准版 暗影黑", 1299, 1093),
    ("全场景", "PC", "荣耀笔记本 X14 Plus 2026 星辰灰", 5899, 5640),
    ("贴膜", "贴膜", "荣耀Magic V5 盾系列钢化膜", 69, 45),
    ("回收", "回收", "回收荣耀Magic6", 1800, 1500),
]
SHORT = {code: short for code, _name, short, _mall, _aliases in STORES}
PAYMENTS = ["银联国补pos", "线下微信", "线下支付宝", "荣耀+微信"]
# 8 月毛利目标：南锣最容易达成，乐堤港最难
TARGETS = {"wangjing": 30000, "nanluo": 20000, "letigang": 60000, "yangguang": 35000}


def build() -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(20260801)
    stores = list(STAFF)
    sales: list[dict[str, Any]] = []
    for month, days in (("2026-07", 31), ("2026-08", 31)):
        year, m = (int(x) for x in month.split("-"))
        for store_index, store in enumerate(stores):
            for day in range(1, days + 1, 2):
                for _ in range(2 + (store_index + day) % 3):
                    ptype, series, name, price, cost = PRODUCTS[rng.randrange(len(PRODUCTS))]
                    date = dt.date(year, m, day)
                    sales.append({
                        "store_code": store,
                        "sale_date": date,
                        "sale_time": dt.time(10 + rng.randrange(10), rng.randrange(60)),
                        "month_label": month,
                        "week_no": (day - 1) // 7 + 1,
                        "salesperson": STAFF[store][rng.randrange(2)],
                        "product_status": "系统已做销售",
                        "product_name": name,
                        "serial_no": f"SAMPLE{len(sales):08d}",
                        "payment_method": PAYMENTS[rng.randrange(len(PAYMENTS))],
                        "recycle_price": cost if ptype == "回收" else None,
                        "sale_price": price,
                        "received_amount": price,
                        "subsidy_receivable": None,
                        "sales_incentive": None,
                        "cost_price": cost,
                        "gross_profit": price - cost,
                        "commission": 30 if ptype in PHONE_TYPES else None,
                        "red_packet": None,
                        "product_type": ptype,
                        "series": series,
                        "is_phone": is_phone(name, ptype, "系统已做销售", price),
                        "remark": None,
                        "invoice_flag": None,
                        "invoice_amount": None,
                        "order_no": None,
                        "source_file": "sample",
                    })
            # 每月一笔赠品：售价 0、毛利为负
            sales.append({**sales[-1], "product_type": None, "series": None, "is_phone": False,
                          "product_name": "荣耀极简背包 黑色", "serial_no": None, "sale_price": 0,
                          "received_amount": 0, "cost_price": 30, "gross_profit": -30, "commission": None,
                          "remark": "同上单赠送"})

    targets = []
    for store in stores:
        profit = sum(r["gross_profit"] for r in sales if r["store_code"] == store and r["month_label"] == "2026-08")
        targets.append({"store_code": store, "month_label": "2026-08", "owner": SHORT[store], "owner_type": "store",
                        "item": "总毛利", "unit": "毛利", "target_value": TARGETS[store], "achieved_value": profit,
                        "as_of": dt.date(2026, 8, 31)})
        for ptype in ("FD", "数字", "X系列", "全场景"):
            done = sum(r["gross_profit"] for r in sales if r["store_code"] == store and r["month_label"] == "2026-08"
                       and r["product_type"] == ptype)
            targets.append({"store_code": store, "month_label": "2026-08", "owner": SHORT[store], "owner_type": "store",
                            "item": ptype, "unit": "毛利", "target_value": TARGETS[store] / 4, "achieved_value": done,
                            "as_of": dt.date(2026, 8, 31)})
        for person in STAFF[store]:
            done = sum(r["gross_profit"] for r in sales if r["store_code"] == store and r["month_label"] == "2026-08"
                       and r["salesperson"] == person)
            targets.append({"store_code": store, "month_label": "2026-08", "owner": person, "owner_type": "staff",
                            "item": "总毛利", "unit": "毛利", "target_value": TARGETS[store] / 2, "achieved_value": done,
                            "as_of": dt.date(2026, 8, 31)})

    stock = []
    for i, store in enumerate(stores):
        for j, (ptype, _series, name, _price, cost) in enumerate(PRODUCTS[:6]):
            stock.append({"store_code": store, "snapshot_date": dt.date(2026, 8, 31), "warehouse": f"{store}库",
                          "category": "手机.荣耀" if ptype in PHONE_TYPES else "融合", "product_name": name,
                          "serial_no": f"STOCK{i}{j}", "quantity": 1, "brand": "荣耀", "supplier": "样例供应商",
                          "cost_price": cost, "in_price": cost, "in_time": dt.datetime(2026, 1, 1) + dt.timedelta(days=30 * j),
                          "age_days": 30 + 40 * j, "model": name.split("-")[0]})

    payments = [{"channel": "微信" if k % 2 else "支付宝", "paid_at": dt.datetime(2026, 8, 1 + k, 12), "store_code": store,
                 "merchant_label": f"荣耀授权体验店（{store}）", "trade_no": f"T{k:06d}", "merchant_order_no": f"M{k:06d}",
                 "trade_type": "JSAPI", "status": "SUCCESS", "amount": 100 + k, "expense": None, "refund_amount": 0,
                 "fee": 0.2, "fee_rate": "0.20%", "balance": None, "remark": None, "source_file": "sample"}
                for k, store in enumerate(stores * 3)]

    policy = [{"month_label": "2026-08", "product_name": name.split("-")[0], "config": "12+512", "store_margin": price - cost,
               "cost_price": cost, "guide_price": price, "guide_commission": 50, "so_incentive": None,
               "promo_price": None, "promo_commission": None, "remark": None}
              for ptype, _s, name, price, cost in PRODUCTS if ptype in PHONE_TYPES]

    return {"fact_store_sales": sales, "fact_store_targets": targets, "fact_stock_age": stock,
            "fact_payments": payments, "dim_product_policy": policy}


def load_sample(conn: Connection) -> dict[str, int]:
    from backend.store_data.loader import write

    ids = dict(conn.execute(text("SELECT store_code, store_id FROM dim_store")).all())
    data = build()
    for rows in data.values():
        for row in rows:
            if "store_code" in row:
                row["store_id"] = ids[row.pop("store_code")]
    conn.execute(text(f"TRUNCATE {', '.join(BUSINESS_TABLES)} RESTART IDENTITY"))
    return write(conn, data)


if __name__ == "__main__":
    from backend.database import get_connection
    from backend.migrations import run_migrations

    run_migrations()
    with get_connection() as c:
        print(load_sample(c))
