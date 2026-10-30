"""
门店数据导入：识别文件类型 → 解析 → （可选）清空旧数据 → 单事务写入 → 更新知识库。

  python -m backend.store_data.loader <文件或目录...> --reset
      --reset        导入前清空：门店业务表、旧演示数据、导入的 csv_ 表、知识库全部页面与文档
      --no-kb        不更新知识库
      --dry-run      只解析并打印识别结果，不写数据库

同一批文件重复导入结果相同。不加 --reset 时只替换本次文件覆盖到的范围（见 REPLACE_SCOPES），
其他月份、门店、单据保持不变，所以每月只需导入新文件；库存、库龄是当前快照，整体替换为最新一次导入。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.store_data.parsers import ParsedFile, parse_file
from backend.store_data.schema import BUSINESS_TABLES, match_store, store_lookup

logger = logging.getLogger("nl2sql.store_data")

SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
# 新数据替换旧数据的范围：旧数据中与本次导入同一范围的行先删除，其余保留。
# 例如导入 9 月的城东门店台账只替换城东 9 月的销售，8 月和其他门店不受影响。
REPLACE_SCOPES: dict[str, tuple[str, ...]] = {
    "fact_store_sales": ("store_id", "month_label"),
    "fact_store_targets": ("store_id", "month_label"),
    "fact_payments": ("channel", "trade_no"),
    "fact_purchase_in": ("doc_no",),
    "dim_product_policy": ("month_label",),
    "dim_product_price": ("product_name",),
}
# 未列出的表（fact_inventory、fact_stock_age）是当前快照，整体替换


def collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES
                                and not p.name.startswith(("~$", "."))))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"找不到文件：{raw}")
    return files


def parse_all(files: list[Path]) -> list[ParsedFile]:
    parsed = []
    for path in files:
        try:
            parsed.append(parse_file(path))
        except Exception as exc:  # 单个文件损坏或格式不对，不影响同批其他文件
            logger.warning("无法读取 %s: %s", path.name, exc)
            parsed.append(ParsedFile(str(path), "无法读取", notes=[f"{path.name}：无法读取（{exc}）"]))
    return parsed


def _resolve_stores(parsed: list[ParsedFile], lookup: dict[str, int]) -> list[str]:
    """填充 store_id。台账文件名里没有门店名时（如「腾讯共享表」），按销售人员在其他台账中所属门店推断。"""
    warnings: list[str] = []
    staff_store: dict[str, Counter] = defaultdict(Counter)
    for item in parsed:
        for row in item.records.get("fact_store_sales", []):
            store_id = match_store(row["store_label"], lookup)
            row["store_id"] = store_id
            if store_id and row["salesperson"]:
                staff_store[row["salesperson"]][store_id] += 1

    for item in parsed:
        sales = item.records.get("fact_store_sales", [])
        if sales and all(r["store_id"] is None for r in sales):
            votes: Counter = Counter()
            for r in sales:
                votes.update(staff_store.get(r["salesperson"] or "", Counter()))
            if votes:
                store_id = votes.most_common(1)[0][0]
                for r in sales:
                    r["store_id"] = store_id
                name = next(k for k, v in lookup.items() if v == store_id)
                warnings.append(f"{Path(item.path).name}：文件名中没有门店名，按销售人员推断为「{name}」")
            else:
                warnings.append(f"{Path(item.path).name}：无法确定门店，销售记录的 store_id 为空")
        for table in ("fact_payments", "fact_stock_age", "fact_store_targets"):
            for row in item.records.get(table, []):
                row["store_id"] = match_store(row.get("store_label"), lookup)
    return warnings


def _merge(parsed: list[ParsedFile]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in parsed:
        for table, rows in item.records.items():
            merged[table].extend(rows)
    # 多个台账附带同一份进店价格表：按商品名去重（后出现的覆盖前面的）
    if merged.get("dim_product_price"):
        merged["dim_product_price"] = list({r["product_name"]: r for r in merged["dim_product_price"]}.values())
    # 同一个人在多个门店有任务时，各门店分别保留
    if merged.get("fact_store_targets"):
        unique = {}
        for r in merged["fact_store_targets"]:
            unique[(r["store_id"], r["month_label"], r["owner"], r["item"], r["unit"])] = r
        merged["fact_store_targets"] = list(unique.values())
    if merged.get("dim_product_policy"):
        unique = {}
        for r in merged["dim_product_policy"]:
            unique[(r["month_label"], r["product_name"], r["config"])] = r
        merged["dim_product_policy"] = list(unique.values())
    return merged


def _table_columns(conn: Connection, table: str) -> list[str]:
    rows = conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :t "
             "ORDER BY ordinal_position"),
        {"t": table},
    ).scalars().all()
    return list(rows)


def reset_data(conn: Connection, include_kb: bool = True) -> dict[str, Any]:
    """清空业务数据、导入的 csv_ 表和（可选）知识库。账号、会话、记忆、模型配置不受影响。"""
    from backend.database import quote_identifier

    summary: dict[str, Any] = {}
    conn.execute(text(f"TRUNCATE {', '.join(BUSINESS_TABLES)} RESTART IDENTITY"))
    imported = conn.execute(text("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name LIKE 'csv\\_%'
    """)).scalars().all()
    for table in imported:
        conn.execute(text(f"DROP TABLE IF EXISTS {quote_identifier(table)}"))
    summary["dropped_imported_tables"] = list(imported)
    if include_kb:
        storage = conn.execute(text("SELECT storage_path FROM kb_documents")).scalars().all()
        pages = conn.execute(text("SELECT COUNT(*) FROM wiki_pages")).scalar()
        conn.execute(text("DELETE FROM wiki_pages"))
        conn.execute(text("DELETE FROM kb_documents"))
        for path in storage:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        summary["deleted_wiki_pages"] = pages
        summary["deleted_documents"] = len(storage)
    return summary


def write(conn: Connection, merged: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, rows in merged.items():
        if not rows:
            continue
        columns = [c for c in _table_columns(conn, table) if c in rows[0]]
        scope = REPLACE_SCOPES.get(table)
        if scope:
            keys = list({tuple(r.get(c) for c in scope) for r in rows})
            where = " AND ".join(f"{c} IS NOT DISTINCT FROM :{c}" for c in scope)
            conn.execute(text(f"DELETE FROM {table} WHERE {where}"), [dict(zip(scope, k)) for k in keys])
        else:
            conn.execute(text(f"TRUNCATE {table} RESTART IDENTITY"))
        placeholders = ", ".join(f":{c}" for c in columns)
        conn.execute(
            text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"),
            [{c: r.get(c) for c in columns} for r in rows],
        )
        counts[table] = len(rows)
    return counts


def load(paths: list[str], reset: bool = False, update_kb: bool = True, dry_run: bool = False,
         wait_index: bool = True) -> dict[str, Any]:
    from backend.database import get_connection

    files = collect_files(paths)
    parsed = parse_all(files)
    report: dict[str, Any] = {
        "files": [{"file": Path(p.path).name, "kind": p.kind,
                   "records": {k: len(v) for k, v in p.records.items()}} for p in parsed],
        "notes": [n for p in parsed for n in p.notes],
    }
    unknown = [Path(p.path).name for p in parsed if not p.records]
    if unknown:
        report["notes"].append("未识别的文件（已跳过）：" + "、".join(unknown))

    with get_connection() as conn:
        lookup = store_lookup(conn)
        report["notes"].extend(_resolve_stores(parsed, lookup))
        merged = _merge(parsed)
        if dry_run:
            report["would_insert"] = {k: len(v) for k, v in merged.items()}
            return report
        if reset:
            report["reset"] = reset_data(conn, include_kb=update_kb)
        report["inserted"] = write(conn, merged)

    if update_kb:
        from backend.store_data.knowledge import rebuild_knowledge

        report["knowledge"] = rebuild_knowledge(files, wait=wait_index)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入门店经营数据（销售台账、收款明细、ERP、库龄表、门店指导书）")
    parser.add_argument("paths", nargs="+", help="文件或目录")
    parser.add_argument("--reset", action="store_true", help="导入前清空旧业务数据、导入表和知识库")
    parser.add_argument("--no-kb", action="store_true", help="不更新知识库")
    parser.add_argument("--dry-run", action="store_true", help="只解析，不写入")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from backend.migrations import run_migrations

    run_migrations()
    report = load(args.paths, reset=args.reset, update_kb=not args.no_kb, dry_run=args.dry_run)
    for item in report["files"]:
        print(f"  {item['file']:<36} {item['kind']:<20} {item['records']}")
    for note in report["notes"]:
        print(f"  ! {note}")
    if "reset" in report:
        print(f"已清空：{report['reset']}")
    print(f"写入：{report.get('inserted') or report.get('would_insert')}")
    if "knowledge" in report:
        print(f"知识库：{report['knowledge']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
