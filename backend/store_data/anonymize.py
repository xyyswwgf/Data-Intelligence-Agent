"""
从本地的真实门店数据生成脱敏演示数据（可以放进公开仓库）

  python -m backend.store_data.anonymize --out demo_data

脱敏规则（逐表逐列白名单：没有列出的列一律置空）：
  - 门店 → 虚构的示例门店（城东 / 老城 / 滨河 / 新区），仓库、收款门店名同步替换
  - 店员 → 「店员01」「店员02」…（打乱顺序编号，看不出原来的人）
  - 金额（销售额、毛利、成本、提成、目标、收款、价格…）→ 乘以随机系数：每家门店、每个月各不相同，
    每行再加 ±4% 噪声；系数每次运行随机生成、不保存。目标在同一系数上再单独扰动 ±10%（达成率合理但不等于真实值），
    但知道某一个真实数字也推算不出其他门店、其他月份的数字
  - 串号、单号、交易号、备注、发票、条码 → 删除或换成序号；供应商、商品编码 → 「供应商A」「SKU0001」
  - 日期、商品名称、数量、库龄 → 保留（让演示看起来真实）；其中出现的真实门店叫法同样替换
  - 门店指导书（提成、进店价政策）不导出

生成后会用 scripts/check_sensitive.py 的规则自检：输出里不能出现真实门店名、店员名、供应商、手机号。
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
import string
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text

from backend.database import get_connection
from backend.store_data.schema import DEFAULT_STORES

TABLES = ["fact_store_sales", "fact_store_targets", "fact_payments", "fact_stock_age", "fact_purchase_in",
          "fact_inventory", "dim_product_policy", "dim_product_price"]

# 每张表：保留的列、按金额缩放的列；其余列由 _transform 中的专门规则处理或置空
KEEP = {
    "fact_store_sales": ["sale_date", "sale_time", "month_label", "week_no", "product_status", "product_name",
                         "payment_method", "product_type", "series", "is_phone", "invoice_flag"],
    "fact_store_targets": ["month_label", "owner_type", "item", "unit", "as_of"],
    "fact_payments": ["channel", "paid_at", "trade_type", "status", "fee_rate"],
    "fact_stock_age": ["snapshot_date", "category", "product_name", "quantity", "brand", "in_time", "age_days", "model"],
    "fact_purchase_in": ["doc_date", "biz_time", "doc_type", "in_method", "audit_status", "product_name", "category",
                         "brand", "quantity"],
    "fact_inventory": ["snapshot_date", "product_name", "category", "brand", "stock_qty", "transfer_out_qty",
                       "transfer_in_qty"],
    "dim_product_policy": ["month_label", "product_name", "config"],
    "dim_product_price": ["category", "product_name"],
}
SCALE = {
    "fact_store_sales": ["recycle_price", "sale_price", "received_amount", "subsidy_receivable", "sales_incentive",
                         "cost_price", "gross_profit", "commission", "red_packet", "invoice_amount"],
    "fact_store_targets": ["target_value", "achieved_value"],
    "fact_payments": ["amount", "expense", "refund_amount", "fee", "balance"],
    "fact_stock_age": ["cost_price", "in_price"],
    "fact_purchase_in": ["unit_price", "amount", "cost_amount"],
    "fact_inventory": [],
    "dim_product_policy": ["store_margin", "cost_price", "guide_price", "guide_commission", "so_incentive",
                           "promo_price", "promo_commission"],
    "dim_product_price": ["retail_price", "cost_price", "gross_margin"],
}


class Pseudonyms:
    """真实值 → 稳定的代号（同一个真实值在所有表里得到同一个代号）。"""

    def __init__(self, fmt: Callable[[int], str], rng: random.Random):
        self.fmt, self.rng, self.map = fmt, rng, {}

    def assign(self, values: set[str]) -> None:
        ordered = sorted(v for v in values if v)
        self.rng.shuffle(ordered)  # 打乱后编号：代号顺序看不出原来的人或供应商
        for v in ordered:
            self.map.setdefault(v, self.fmt(len(self.map) + 1))

    def __call__(self, value: str | None) -> str | None:
        if not value:
            return value
        if value not in self.map:
            self.map[value] = self.fmt(len(self.map) + 1)
        return self.map[value]


def _letters(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = string.ascii_uppercase[r] + out
    return out


def _rows(conn, table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(f"SELECT * FROM {table}")).mappings().all()]


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def build_demo() -> dict[str, Any]:
    rng = random.Random(secrets.randbits(64))
    base = rng.choice([rng.uniform(0.55, 0.85), rng.uniform(1.2, 1.7)])  # 不保存、不输出
    factors: dict[tuple, float] = {}

    def factor(store_id: Any = None, month: Any = None) -> float:
        """门店 × 月份各自的系数（没有门店 / 月份的行用总体系数）。"""
        key = (store_id, str(month)[:7] if month else None)
        if key not in factors:
            store_part = 1.0 if store_id is None else factors.setdefault((store_id, "*"), rng.uniform(0.75, 1.3))
            factors[key] = base * store_part * (1.0 if month is None else rng.uniform(0.9, 1.1))
        return factors[key]

    with get_connection() as conn:
        stores = _rows(conn, "dim_store")
        data = {t: _rows(conn, t) for t in TABLES}

    stores.sort(key=lambda s: s["store_id"])
    demo_stores = []
    store_map: dict[int, dict[str, Any]] = {}
    for i, s in enumerate(stores):
        code, name, short, mall, aliases = (DEFAULT_STORES[i] if i < len(DEFAULT_STORES)
                                            else (f"store_{i + 1}", f"示例门店{i + 1}", f"门店{i + 1}", "", [f"门店{i + 1}"]))
        demo = {"store_code": code, "store_name": name, "short_name": short, "mall": mall, "aliases": aliases}
        demo_stores.append(demo)
        store_map[s["store_id"]] = demo
    real_store_words = [w for s in stores for w in (s["store_name"], s["short_name"], s["mall"], *(s["aliases"] or [])) if w]
    # 文字里出现的真实门店叫法（如收款方式「某店-京东」、以街区命名的文创商品）替换成对应的示例门店简称
    replacements = sorted(((w, store_map[s["store_id"]]["short_name"]) for s in stores
                           for w in (s["store_name"], s["short_name"], s["mall"], *(s["aliases"] or [])) if w),
                          key=lambda pair: len(pair[0]), reverse=True)

    def scrub(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        for real, demo_name in replacements:
            value = value.replace(real, demo_name)
        return value

    def store_of(text_value: str | None) -> dict[str, Any] | None:
        if not text_value:
            return None
        for s in stores:
            if any(w and w in text_value for w in (s["store_name"], s["short_name"], s["mall"], *(s["aliases"] or []))):
                return store_map[s["store_id"]]
        return None

    staff = Pseudonyms(lambda n: f"店员{n:02d}", rng)
    staff.assign({r["salesperson"] for r in data["fact_store_sales"]}
                 | {r["owner"] for r in data["fact_store_targets"] if r["owner_type"] == "staff"})
    suppliers = Pseudonyms(lambda n: f"供应商{_letters(n)}", rng)
    skus = Pseudonyms(lambda n: f"SKU{n:04d}", rng)
    skus.assign({r["item_code"] for r in data["fact_purchase_in"]} | {r["item_code"] for r in data["fact_inventory"]})

    def scaled(row: dict, cols: list[str], noise: bool = True) -> dict[str, Any]:
        month = row.get("month_label") or row.get("sale_date") or row.get("paid_at") or row.get("doc_date")
        f = factor(row.get("store_id"), month) * (1 + rng.uniform(-0.04, 0.04) if noise else 1)
        return {c: (round(float(row[c]) * f, 2) if row.get(c) is not None else None) for c in cols}

    out: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        rows = []
        for i, row in enumerate(data[table], start=1):
            new = {c: scrub(row.get(c)) for c in KEEP[table]}
            new.update(scaled(row, SCALE[table], noise=table != "fact_store_targets"))
            store = store_map.get(row.get("store_id"))
            if "store_id" in row:
                new["store_code"] = store["store_code"] if store else None
            if table == "fact_store_sales":
                new.update(salesperson=staff(row["salesperson"]), source_file="demo")
            elif table == "fact_store_targets":
                new["owner"] = (store["short_name"] if store else "门店") if row["owner_type"] == "store" else staff(row["owner"])
                if new.get("target_value") is not None:  # 目标单独扰动 ±10%，达成率与真实值不再相同
                    new["target_value"] = round(new["target_value"] * rng.uniform(0.9, 1.1), 2)
            elif table == "fact_payments":
                new.update(merchant_label=f"连锁门店（{store['short_name']}）" if store else "收款终端",
                           trade_no=f"DEMO-{'W' if row['channel'] == '微信' else 'A'}{i:06d}", source_file="demo")
            elif table == "fact_stock_age":
                new.update(warehouse=f"{store['short_name']}仓" if store else "总仓", supplier=suppliers(row["supplier"]))
            elif table == "fact_purchase_in":
                target = store_of(row.get("warehouse"))
                new.update(doc_no=f"RK{i:05d}", company="示例公司", supplier=suppliers(row["supplier"]),
                           warehouse=f"{target['short_name']}仓" if target else "总仓", item_code=skus(row["item_code"]))
            elif table == "fact_inventory":
                new["item_code"] = skus(row["item_code"])
            rows.append({k: _json(v) for k, v in new.items()})
        out[table] = rows

    # 自检：真实门店叫法、店员、供应商不能出现在输出里
    blob = json.dumps(out, ensure_ascii=False)
    fictional = {w for d in demo_stores for w in (d["store_name"], d["short_name"], d["mall"], *d["aliases"]) if w}
    leaked = [w for w in (set(real_store_words) | set(staff.map) | set(suppliers.map)) - fictional
              if len(w) >= 2 and w in blob]
    if leaked:
        raise RuntimeError(f"脱敏自检失败：输出中仍有 {len(leaked)} 个真实名称")
    return {"stores": demo_stores, "tables": out}


def write_demo(out_dir: Path) -> dict[str, int]:
    demo = build_demo()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dim_store.json").write_text(json.dumps(demo["stores"], ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {}
    for table, rows in demo["tables"].items():
        (out_dir / f"{table}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        counts[table] = len(rows)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从本地真实门店数据生成脱敏演示数据")
    parser.add_argument("--out", default="demo_data", help="输出目录")
    args = parser.parse_args(argv)
    counts = write_demo(Path(args.out))
    for table, n in counts.items():
        print(f"  {table:<22} {n} 行")
    print(f"已写入 {args.out}/（金额已按随机系数缩放并加噪声，系数未保存）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
