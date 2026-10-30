"""
门店知识库：把门店指导书转换成 Wiki 页面，并根据已导入的数据生成「门店与数据说明」「月度任务」页面。

  - 每份指导书一个父页面（2026年8月门店指导书），每个工作表一个子页面
  - 产品政策按机型分节（每节一张小表），检索时能直接命中具体机型
  - 其他工作表（考核、清尾、运营商、奖惩）逐行转成「表头：值」的条目，避免整张大表成为一个检索块

重复执行时按 slug 更新已有页面（保留版本历史），不会产生重复页面。
"""

from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.database import get_connection
from backend.excel_importer import read_workbook
from backend.store_data.parsers import _month_from, _number, _text, parse_product_policy
from backend.store_data.schema import brand

HOME_SLUG = "home"
DATA_GUIDE_SLUG = "store-data-guide"


# ---------------------------------------------------------------------------
# 页面写入
# ---------------------------------------------------------------------------


def _upsert(slug: str, title: str, content: str, tags: list[str], parent_id: int | None, order: int = 0) -> int:
    from backend.kb import wiki

    with get_connection() as conn:
        row = conn.execute(text("SELECT id, version, content_md FROM wiki_pages WHERE slug = :s AND NOT is_deleted"),
                           {"s": slug}).mappings().first()
        if row is None:
            page = wiki.create_page(title, content, None, parent_id, tags, slug, change_note="门店数据导入", conn=conn)
            page_id = page["id"]
            changed = True
        else:
            page_id = row["id"]
            changed = row["content_md"] != content
        conn.execute(text("UPDATE wiki_pages SET sort_order = :o WHERE id = :id"), {"o": order, "id": page_id})
    if row is not None and changed:
        wiki.update_page(page_id, None, row["version"], title=title, content_md=content, parent_id=parent_id,
                         tags=tags, change_note="门店数据导入：内容更新")
    return page_id


# ---------------------------------------------------------------------------
# 工作表 → Markdown
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    s = _text(value)
    if s is None:
        return ""
    n = _number(value)
    if n is not None and isinstance(value, float) and 0 < abs(n) < 0.2:
        return f"{n:.0%}"  # 0.06 → 6%（店提 / 提成点位）；0.5 这类分值保持原样
    return re.sub(r"([：:；])；", r"\1", re.sub(r"\s*\n\s*", "；", s)).strip("；")


def _table(block: list[list[str]]) -> list[str]:
    """连续的多列行渲染成一张小表，第一行作表头；去掉整列为空的左侧列，合并单元格留空。"""
    offset = min(next(i for i, c in enumerate(r) if c) for r in block)
    rows = [r[offset:] for r in block]
    width = max(len(r) for r in rows)
    rows = [[c.replace("|", "/") for c in r] + [""] * (width - len(r)) for r in rows]
    return (["", "| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
            + ["| " + " | ".join(r) + " |" for r in rows[1:]] + [""])


def sheet_to_markdown(rows: list[list[Any]]) -> str:
    """
    逐行保留工作表内容：只有一个单元格的行是标题或说明文字，连续的多列行是一张小表。
    指导书的考核、运营商等工作表不是规整的二维表，不能假设第一行就是表头。
    """
    lines: list[str] = []
    block: list[list[str]] = []

    def flush() -> None:
        if block:
            lines.extend(_table(block) if len(block) > 1 else ["- " + " ｜ ".join(c for c in block[0] if c)])
            block.clear()

    for row in rows:
        cells = [_cell(v) for v in row]
        while cells and not cells[-1]:
            cells.pop()
        filled = [c for c in cells if c]
        if not filled:
            flush()
            continue
        if len(filled) > 1:
            block.append(cells)
            continue
        flush()
        value = filled[0]
        if len(value) <= 16 and not re.search(r"[，。；：:、（(]", value):
            lines.extend(["", f"### {value}", ""])
        else:
            lines.append(f"- {value}" if re.match(r"^\d+[、.]", value) else value)
    flush()
    return "\n".join(lines).strip() + "\n"


def policy_to_markdown(records: list[dict[str, Any]]) -> str:
    groups: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for r in records:
        groups.setdefault(r["product_name"], []).append(r)

    def money(v: Any) -> str:
        return "—" if v is None else f"{v:,.0f}"

    lines = [
        "字段说明：进店价为门店成本；常规指导价为零售价；提成为按该价格成交的单台提成；"
        "SO 激励为厂家激励，**进店价 − SO 激励 = 本月实际进店价**；活动促销价有单独提成。",
        "",
    ]
    for name, items in groups.items():
        lines += [f"### {name}", "", "| 配置 | 进店价 | 进店毛利 | 常规指导价 | 指导价提成 | SO激励 | 活动促销价 | 促销价提成 | 备注 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in items:
            lines.append("| " + " | ".join([
                r["config"] or "—", money(r["cost_price"]), money(r["store_margin"]), money(r["guide_price"]),
                money(r["guide_commission"]), money(r["so_incentive"]), money(r["promo_price"]),
                money(r["promo_commission"]), (r["remark"] or "").replace("|", "/"),
            ]) + " |")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 指导书 / 数据说明 / 月度任务
# ---------------------------------------------------------------------------


def _guide_books(files: list[Path]) -> list[tuple[str, Path, dict[str, list[list[Any]]]]]:
    books = []
    for path in files:
        month = _month_from(path.stem)
        if not month:
            continue
        try:
            sheets = read_workbook(path.read_bytes())
        except ValueError:
            continue
        if "产品政策" in sheets or "门店指导书" in path.stem:
            books.append((month, path, sheets))
    return sorted(books, key=lambda b: b[0])


def _month_title(month: str) -> str:
    year, m = month.split("-")
    return f"{year}年{int(m)}月"


def _write_guide(month: str, sheets: dict[str, list[list[Any]]], home_id: int, order: int) -> list[int]:
    title = f"{_month_title(month)}门店指导书"
    slug = f"store-guide-{month}"
    children = [name for name, rows in sheets.items() if any(any(v is not None for v in r) for r in rows)]
    intro = (f"# {title}\n\n本月门店经营政策，包括产品进店价与提成、考核规则、运营商业务等。\n\n"
             + "\n".join(f"- [[{slug}-{i + 1}]] {name}" for i, name in enumerate(children))
             + f"\n\n数据口径见 [[{DATA_GUIDE_SLUG}]]。\n")
    parent_id = _upsert(slug, title, intro, ["门店指导书", _month_title(month)], home_id, order)
    ids = [parent_id]
    for i, name in enumerate(children):
        rows = sheets[name]
        if name == "产品政策":
            body = policy_to_markdown(parse_product_policy(rows, month))
            tags = ["产品政策", "进店价", "提成", _month_title(month)]
        else:
            body = sheet_to_markdown(rows)
            tags = [name, _month_title(month)]
        content = f"# {_month_title(month)} · {name}\n\n{body}\n返回 [[{slug}]]\n"
        ids.append(_upsert(f"{slug}-{i + 1}", f"{_month_title(month)} · {name}", content, tags, parent_id, i))
    return ids


def _data_guide() -> str:
    with get_connection() as conn:
        stores = conn.execute(text("""
            SELECT d.short_name, d.store_name, d.mall, d.aliases,
                   (SELECT string_agg(DISTINCT s.salesperson, '、') FROM fact_store_sales s
                     WHERE s.store_id = d.store_id AND s.salesperson IS NOT NULL) AS staff,
                   (SELECT string_agg(DISTINCT a.warehouse, '、') FROM fact_stock_age a WHERE a.store_id = d.store_id) AS warehouse
            FROM dim_store d ORDER BY d.store_id
        """)).mappings().all()
        coverage = conn.execute(text("""
            SELECT d.short_name, s.month_label, MIN(s.sale_date)::text AS first_day, MAX(s.sale_date)::text AS last_day,
                   COUNT(*) AS rows, ROUND(SUM(s.sale_price), 2) AS sales, ROUND(SUM(s.gross_profit), 2) AS profit
            FROM fact_store_sales s JOIN dim_store d ON d.store_id = s.store_id
            GROUP BY d.short_name, d.store_id, s.month_label ORDER BY s.month_label, d.store_id
        """)).mappings().all()
        counts = {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in (
            "fact_payments", "fact_purchase_in", "fact_inventory", "fact_stock_age", "fact_store_targets",
            "dim_product_policy", "dim_product_price")}
        unmatched = conn.execute(text("""
            SELECT merchant_label, COUNT(*) AS n FROM fact_payments WHERE store_id IS NULL AND merchant_label IS NOT NULL
            GROUP BY merchant_label ORDER BY n DESC LIMIT 8
        """)).all()

    lines = ["# 门店与数据说明", "", "## 门店", "",
             "| 门店 | 全称 | 商场 | 仓库 | 店员 |", "|---|---|---|---|---|"]
    for s in stores:
        lines.append(f"| {s['short_name']} | {brand()}（{s['store_name']}） | {s['mall'] or '—'} | "
                     f"{s['warehouse'] or '—'} | {s['staff'] or '—'} |")
    lines += ["", "## 指标口径", "",
              "- **销售额** = 销售台账 `fact_store_sales.sale_price` 求和（含回收业务；回收的销售价格是回收平台给公司的价格）。"
              "台账中「系统已调总库」「系统已做批发」的行不是门店零售，不导入，因此有这类行的门店会比「月度任务」表中的销售额略低。",
              "- **毛利** = `gross_profit` 求和；赠品、随单膜等售价为 0 的行毛利为负（计入成本）。同样不含调回总库 / 批发。",
              "- **毛利率** = 毛利 ÷ 销售额。",
              "- **手机台数** = 商品名称含「全网通」的手机行数（按商品名称判断，不依赖手填的「类型」列）；"
              "不含平板、旧机回收和售价为 0 的赠品，线上订单的手机计入。"
              "因此与门店周报中按「类型」统计的台数可能相差几台。",
              "- **手机销售额** = 上述手机行的销售价格之和。门店销售额还包括配件、全场景、文创、回收等，"
              "手机台数多不代表销售额高，需结合手机销售占比和手机均价看。",
              "- **毛利达成率** = 毛利 ÷ 月度任务中的毛利目标；月中按「已过天数 ÷ 当月天数」预估月底达成。",
              "- **国补**：收款方式为「银联国补pos」等的订单，实际到账 = 销售价格 − 国补应返，国补部分后续返还。",
              "- **长库龄**：库龄表中库龄超过 90 天的在库商品。",
              "- **店长店提 / 店员提成**按门店毛利所在档位计算，档位见各月门店指导书的「店长店员考核」。",
              "", "## 数据覆盖", "",
              "| 月份 | 门店 | 日期范围 | 台账行数 | 销售额 | 毛利 |", "|---|---|---|---|---|---|"]
    for c in coverage:
        lines.append(f"| {c['month_label']} | {c['short_name']} | {c['first_day']} ~ {c['last_day']} | {c['rows']} | "
                     f"{float(c['sales']):,.2f} | {float(c['profit']):,.2f} |")
    lines += ["", "其他数据：" + "，".join(f"`{t}` {n} 行" for t, n in counts.items()) + "。", "",
              "## 数据表", "",
              "- `fact_store_sales` 门店销售台账（每行一个商品）",
              "- `fact_store_targets` 月度任务：门店和店员按产品线的毛利 / 台数目标与完成",
              "- `fact_payments` 微信 / 支付宝收款明细（对账用）",
              "- `fact_purchase_in` ERP 采购入库明细；`fact_inventory` ERP 库存快照（全公司）",
              "- `fact_stock_age` 库龄表（按门店仓库、串号）",
              "- `dim_product_policy` 门店指导书 · 产品政策；`dim_product_price` 进店价格表（SKU 级）",
              "- `dim_store` 门店维度"]
    if unmatched:
        lines += ["", "## 待确认", "",
                  "以下收款终端没有对应到门店（`fact_payments.store_id` 为空），确认后可在 `dim_store.aliases` 中补充：", ""]
        lines += [f"- {label}：{n} 笔" for label, n in unmatched]
    return "\n".join(lines) + "\n"


def _targets_page(month: str) -> str | None:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT d.short_name AS store, t.owner, t.owner_type, t.item, t.unit, t.target_value, t.achieved_value, t.as_of
            FROM fact_store_targets t JOIN dim_store d ON d.store_id = t.store_id
            WHERE t.month_label = :m
            ORDER BY d.store_id, t.owner_type, t.owner, t.target_id
        """), {"m": month}).mappings().all()
    if not rows:
        return None
    as_of = next((r["as_of"] for r in rows if r["as_of"]), None)
    lines = [f"# {_month_title(month)}门店月度任务", "",
             f"来源：各门店销售报表中的「月度任务」表{f'，统计截至 {as_of}' if as_of else ''}。毛利目标的口径见 [[{DATA_GUIDE_SLUG}]]。", ""]
    by_store: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        by_store.setdefault(r["store"], []).append(r)
    for store, items in by_store.items():
        lines += [f"## {store}", ""]
        owners: "OrderedDict[str, list]" = OrderedDict()
        for r in items:
            owners.setdefault(r["owner"], []).append(r)
        for owner, entries in owners.items():
            kind = "门店" if entries[0]["owner_type"] == "store" else "店员"
            lines += [f"### {owner}（{kind}）", "", "| 考核项 | 单位 | 目标 | 完成 | 达成率 |", "|---|---|---|---|---|"]
            for e in entries:
                target, done = e["target_value"], e["achieved_value"]
                rate = f"{float(done) / float(target):.0%}" if target and done is not None else "—"
                unit = "毛利(元)" if e["unit"] == "毛利" else e["unit"]
                lines.append(f"| {e['item']} | {unit} | {_fmt(target)} | {_fmt(done)} | {rate} |")
            lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    return "—" if value is None else f"{float(value):,.2f}".rstrip("0").rstrip(".")


def rebuild_knowledge(files: list[Path], wait: bool = True) -> dict[str, Any]:
    """
    重建门店相关的知识库页面。wait=True 时逐页建索引后返回（命令行导入）；
    wait=False 时只把需要索引的页面交给后台索引队列（网页导入，避免请求等待向量化）。
    """
    from backend.kb.indexer import index_page, submit_index

    with get_connection() as conn:
        store_list = "、".join(conn.execute(text("SELECT store_name FROM dim_store ORDER BY store_id")).scalars().all())
    home = (
        f"# 知识库首页\n\n{brand()}（{store_list}）经营知识库。\n\n"
        f"- [[{DATA_GUIDE_SLUG}]] 门店与数据说明：门店、店员、指标口径、数据覆盖\n"
    )
    home_id = _upsert(HOME_SLUG, "知识库首页", home, ["导航"], None)
    page_ids = [_upsert(DATA_GUIDE_SLUG, "门店与数据说明", _data_guide(), ["门店", "口径", "数据说明"], home_id, 0)]

    with get_connection() as conn:
        months = conn.execute(text("SELECT DISTINCT month_label FROM fact_store_targets ORDER BY 1")).scalars().all()
    for i, month in enumerate(months):
        content = _targets_page(month)
        if content:
            page_ids.append(_upsert(f"store-targets-{month}", f"{_month_title(month)}门店月度任务", content,
                                    ["月度任务", "目标", _month_title(month)], home_id, 10 + i))
            home += f"- [[store-targets-{month}]] {_month_title(month)}门店月度任务\n"

    for month, _path, sheets in _guide_books(files):
        page_ids += _write_guide(month, sheets, home_id, int(month.replace("-", "")))  # 按月份排序
    # 首页链接到所有月份的指导书，包括以前导入、这次没有重新导入的
    with get_connection() as conn:
        guides = conn.execute(text(
            "SELECT slug, title FROM wiki_pages WHERE slug ~ '^store-guide-[0-9]{4}-[0-9]{2}$' ORDER BY slug")).all()
    for slug, title in guides:
        home += f"- [[{slug}]] {title}\n"

    _upsert(HOME_SLUG, "知识库首页", home, ["导航"], None)
    page_ids.insert(0, home_id)

    if not wait:
        # 内容有变化的页面在保存时已提交索引；这里补上之前没有索引成功的页面
        with get_connection() as conn:
            pending = conn.execute(text("SELECT id FROM wiki_pages WHERE id = ANY(:ids) AND index_status <> 'ready'"),
                                   {"ids": list(set(page_ids))}).scalars().all()
        for pid in pending:
            submit_index(pid)
        return {"pages": len(set(page_ids)), "queued": len(pending)}
    statuses = [index_page(pid) for pid in dict.fromkeys(page_ids)]
    return {"pages": len(set(page_ids)), "indexed": statuses.count("ready"),
            "partial": len(statuses) - statuses.count("ready")}
