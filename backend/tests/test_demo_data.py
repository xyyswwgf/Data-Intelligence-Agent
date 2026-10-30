"""脱敏演示数据：生成规则（不泄露店员、串号、真实金额）与仓库中的 demo_data/ 可以直接导入分析。"""

import json
import re
from pathlib import Path

from backend.tests.conftest import requires_db

DEMO = Path(__file__).resolve().parents[2] / "demo_data"


def test_demo_files_have_no_personal_data():
    blob = "".join(p.read_text(encoding="utf-8") for p in DEMO.glob("*.json"))
    assert not re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", blob)  # 手机号
    sales = json.loads((DEMO / "fact_store_sales.json").read_text(encoding="utf-8"))
    assert all(not {"serial_no", "remark", "order_no"} & set(r) for r in sales)
    assert {r["salesperson"] for r in sales if r["salesperson"]} <= {f"店员{n:02d}" for n in range(1, 100)}
    stores = json.loads((DEMO / "dim_store.json").read_text(encoding="utf-8"))
    assert [s["short_name"] for s in stores] == ["城东", "老城", "滨河", "新区"]


@requires_db
def test_anonymize_replaces_names_and_amounts(client):
    from sqlalchemy import text

    from backend.database import get_connection
    from backend.store_data.anonymize import build_demo

    with get_connection() as conn:
        real_staff = set(conn.execute(text("SELECT DISTINCT salesperson FROM fact_store_sales")).scalars().all())
        real_total = float(conn.execute(text("SELECT SUM(sale_price) FROM fact_store_sales")).scalar())
    demo = build_demo()
    sales = demo["tables"]["fact_store_sales"]
    assert not real_staff & {r["salesperson"] for r in sales}
    assert abs(sum(r["sale_price"] or 0 for r in sales) - real_total) > 1  # 金额已缩放
    assert all(r.get("serial_no") is None for r in sales)
    assert all(p["trade_no"].startswith("DEMO-") for p in demo["tables"]["fact_payments"])


@requires_db
def test_demo_data_loads_and_analyses(client, admin_headers):
    from backend.database import get_connection
    from backend.store_data.demo import load_demo
    from backend.store_data.sample import load_sample

    try:
        with get_connection() as conn:
            counts = load_demo(conn)
        assert counts["fact_store_sales"] > 1000 and counts["fact_store_targets"] > 0
        body = client.post("/api/analyze", headers=admin_headers, json={"question": "本月各门店经营怎么样？"}).json()
        assert body["status"] == "completed"
        assert {s["门店"] for s in body["attribution"]["stores"]} <= {"城东", "老城", "滨河", "新区"}
    finally:
        with get_connection() as conn:
            load_sample(conn)
