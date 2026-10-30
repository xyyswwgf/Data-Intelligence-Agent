"""
导入脱敏演示数据（demo_data/，由 anonymize.py 从真实数据生成）

  python -m backend.store_data.demo --yes

会清空当前数据库的门店业务表，把门店换成演示门店，再生成知识库中的「门店与数据说明」「门店月度任务」页面。
只在演示环境使用，不要对存有真实数据的数据库执行。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.store_data.schema import BUSINESS_TABLES

DEMO_DIR = Path(__file__).resolve().parents[2] / "demo_data"


def load_demo(conn: Connection, demo_dir: Path = DEMO_DIR) -> dict[str, int]:
    from backend.store_data.anonymize import TABLES
    from backend.store_data.loader import write

    stores = json.loads((demo_dir / "dim_store.json").read_text(encoding="utf-8"))
    conn.execute(text(f"TRUNCATE {', '.join(BUSINESS_TABLES)} RESTART IDENTITY"))
    conn.execute(text("DELETE FROM dim_store WHERE store_code <> ALL(:codes)"), {"codes": [s["store_code"] for s in stores]})
    for s in stores:
        conn.execute(text("""
            INSERT INTO dim_store (store_code, store_name, short_name, mall, aliases)
            VALUES (:store_code, :store_name, :short_name, :mall, :aliases)
            ON CONFLICT (store_code) DO UPDATE SET store_name = EXCLUDED.store_name, short_name = EXCLUDED.short_name,
                mall = EXCLUDED.mall, aliases = EXCLUDED.aliases
        """), s)
    ids = dict(conn.execute(text("SELECT store_code, store_id FROM dim_store")).all())
    data = {}
    for table in TABLES:
        rows = json.loads((demo_dir / f"{table}.json").read_text(encoding="utf-8"))
        for row in rows:
            if "store_code" in row:
                row["store_id"] = ids.get(row.pop("store_code"))
        data[table] = rows
    return write(conn, data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入脱敏演示数据（会清空门店业务表）")
    parser.add_argument("--yes", action="store_true", help="确认清空当前数据库的门店业务数据")
    parser.add_argument("--no-kb", action="store_true", help="不生成知识库页面")
    args = parser.parse_args(argv)
    if not args.yes:
        raise SystemExit("这会清空当前数据库的门店业务数据，确认请加 --yes")

    from backend.database import get_connection
    from backend.migrations import run_migrations

    run_migrations()
    with get_connection() as conn:
        counts = load_demo(conn)
    for table, n in counts.items():
        print(f"  {table:<22} {n} 行")
    if not args.no_kb:
        from backend.store_data.knowledge import rebuild_knowledge

        print(f"知识库：{rebuild_knowledge([])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
