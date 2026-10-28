"""
门店经营分析 Agent — 基于 LangGraph 的确定性分析工作流

  planner → knowledge → sql → executor → analysis → attribution → visualization → report

分析对象是荣耀授权体验店的销售台账（fact_store_sales）、月度任务（fact_store_targets）和库龄表（fact_stock_age）：
  - 门店：销售额、毛利、毛利率、手机台数，对照毛利目标计算达成率；月中按已过天数预估月底达成
  - 结构：产品线（类型）、店员、收款方式，定位贡献最大和差距最大的部分
  - 库存：长库龄（> 90 天）商品的数量和金额

设计取舍:
  - 不依赖 LLM：SQL 由模板生成，结论可复现、可审计，适合作为"可信基线"
  - 分析月份从数据中自动识别（最新月份），也支持问题中显式指定月份和门店
  - 任何节点失败后，下游节点自动跳过，错误定位到具体步骤
"""

from __future__ import annotations

import calendar
import logging
import re
import uuid
from typing import Any, TypedDict

try:
    from langgraph.graph import END, StateGraph
except Exception:  # LangGraph is optional at runtime; the fallback remains deterministic.
    END = None
    StateGraph = None

from backend.agent_common import (
    bar_chart,
    execute_queries,
    get_history_detail,
    line_chart,
    list_history,
    render_markdown,
    rows_of,
    run_node,
    safe_rate,
    save_analysis_result,
)
from backend.database import execute_sql

logger = logging.getLogger("nl2sql.agent")

__all__ = ["run_analysis", "list_history", "get_history_detail", "export_report", "save_analysis_result"]

_MONTH_LABEL = re.compile(r"^\d{4}-\d{2}$")
LONG_STOCK_DAYS = 90

_FOCUS_KEYWORDS = {
    "target": ("达成", "目标", "任务", "完成率", "差距", "差额"),
    "staff": ("店员", "员工", "人员", "提成", "红包", "个人", "谁"),
    "stock": ("库存", "库龄", "压货", "积压", "清尾", "滞销"),
    "product": ("产品线", "系列", "机型", "品类", "类型", "结构", "折叠", "配件"),
}


class AgentState(TypedDict, total=False):
    question: str
    analysis_mode: str
    run_id: str
    period: dict[str, str]
    stores: list[dict[str, Any]]
    focus: str
    steps: list[dict[str, Any]]
    plan: list[dict[str, str]]
    knowledge: list[dict[str, str]]
    sql_queries: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    attribution: dict[str, Any]
    charts: list[dict[str, Any]]
    report: dict[str, Any]
    errors: list[dict[str, str]]


_COMPILED_GRAPH = None


def run_analysis(question: str, analysis_mode: str = "auto", user_id: int | None = None) -> dict[str, Any]:
    state: AgentState = {
        "question": question.strip(),
        "analysis_mode": analysis_mode,
        "run_id": str(uuid.uuid4()),
        "period": {},
        "stores": [],
        "focus": "overview",
        "steps": [],
        "plan": [],
        "knowledge": [],
        "sql_queries": [],
        "metrics": [],
        "attribution": {},
        "charts": [],
        "report": {},
        "errors": [],
    }

    graph = _get_graph()
    if graph is not None:
        state = graph.invoke(state)
    else:
        for node in _NODES:
            state = node(state)

    response = {
        "question": state["question"],
        "run_id": state["run_id"],
        "status": "failed" if state.get("errors") else "completed",
        "period": state.get("period", {}),
        "steps": state.get("steps", []),
        "plan": state.get("plan", []),
        "knowledge": state.get("knowledge", []),
        "sql_queries": state.get("sql_queries", []),
        "metrics": state.get("metrics", []),
        "attribution": state.get("attribution", {}),
        "charts": state.get("charts", []),
        "report": state.get("report", {}),
        "errors": state.get("errors", []),
    }
    save_analysis_result(response, user_id)
    return response


def export_report(run_id: str, export_format: str = "markdown", user_id: int | None = None) -> tuple[str, str, str] | None:
    detail = get_history_detail(run_id, user_id)
    if not detail:
        return None

    report = detail.get("report", {})
    markdown = report.get("markdown") or render_markdown(
        "门店经营分析报告",
        report.get("summary", ""),
        report.get("findings", []),
        report.get("recommendations", []),
    )
    safe_run_id = re.sub(r"[^\w-]", "_", run_id)
    if export_format == "html":
        html = _html_report(detail, markdown)
        return html, "text/html; charset=utf-8", f"analysis_{safe_run_id}.html"
    return markdown, "text/markdown; charset=utf-8", f"analysis_{safe_run_id}.md"


def _get_graph():
    """编译后的图是无状态的，进程内复用一份，避免每个请求重复编译。"""
    global _COMPILED_GRAPH
    if StateGraph is None:
        return None
    if _COMPILED_GRAPH is None:
        graph = StateGraph(AgentState)
        names = [node.__name__.removesuffix("_node") for node in _NODES]
        for name, node in zip(names, _NODES):
            graph.add_node(name, node)
        graph.set_entry_point(names[0])
        for src, dst in zip(names, names[1:]):
            graph.add_edge(src, dst)
        graph.add_edge(names[-1], END)
        _COMPILED_GRAPH = graph.compile()
    return _COMPILED_GRAPH


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------


def planner_node(state: AgentState) -> AgentState:
    def work() -> str:
        question = state["question"]
        current, previous, last_day = _resolve_period(question)
        state["period"] = {"current": current, "previous": previous, "last_day": last_day}
        state["stores"] = _resolve_stores(question)

        focus = "overview"
        for name, words in _FOCUS_KEYWORDS.items():
            if any(word in question for word in words):
                focus = name
                break
        state["focus"] = focus

        scope = "、".join(s["short_name"] for s in state["stores"]) or "全部门店"
        plan = [
            {"id": "store_overview", "purpose": f"{scope} {current} 销售额、毛利、毛利率、手机台数"},
            {"id": "store_targets", "purpose": "对照月度毛利目标计算达成率，月中预估月底达成"},
            {"id": "type_mix", "purpose": "按产品线拆解销售额和毛利"},
            {"id": "item_targets", "purpose": "找出与产品线目标差距最大的部分"},
            {"id": "staff", "purpose": "店员销售、毛利、提成与个人目标"},
            {"id": "payment_mix", "purpose": "收款方式结构（国补、微信、支付宝、京东等）"},
            {"id": "stock_age", "purpose": f"门店在库商品库龄，统计超过 {LONG_STOCK_DAYS} 天的长库龄库存"},
        ]
        if previous != current:
            plan.insert(1, {"id": "mom", "purpose": f"与 {previous} 环比"})
        state["plan"] = plan
        return f"分析 {scope} {current}（数据截至 {last_day}），关注点 {focus}，生成 {len(plan)} 个分析步骤。"

    return run_node(state, "planner", work)


def knowledge_node(state: AgentState) -> AgentState:
    def work() -> str:
        # 检索企业知识库里的门店指导书、考核规则、指标口径；不可用时不影响数据分析
        try:
            from backend.kb.rag import knowledge_for_agent

            state["knowledge"] = knowledge_for_agent(state["question"])
        except Exception as exc:
            logger.warning("企业知识库检索失败: %s", exc)
            state["knowledge"] = []
        return f"从企业知识库检索到 {len(state['knowledge'])} 条相关知识。"

    return run_node(state, "knowledge", work)


def sql_node(state: AgentState) -> AgentState:
    def work() -> str:
        months = _months_in_clause(state["period"])
        current = _months_in_clause({"current": state["period"]["current"], "previous": state["period"]["current"]})
        ids = [int(s["store_id"]) for s in state["stores"]]
        store_filter = f" AND s.store_id IN ({', '.join(map(str, ids))})" if ids else ""
        stock_filter = f" AND a.store_id IN ({', '.join(map(str, ids))})" if ids else ""
        target_filter = f" AND t.store_id IN ({', '.join(map(str, ids))})" if ids else ""
        state["sql_queries"] = [
            {
                "id": "store_overview",
                "purpose": "门店月度核心指标",
                "sql": f"""
                    SELECT d.short_name AS "门店", s.month_label AS "月份",
                           ROUND(SUM(s.sale_price), 2) AS "销售额",
                           ROUND(SUM(s.gross_profit), 2) AS "毛利",
                           ROUND(SUM(s.gross_profit) / NULLIF(SUM(s.sale_price), 0), 4) AS "毛利率",
                           COUNT(*) FILTER (WHERE s.is_phone) AS "手机台数",
                           ROUND(COALESCE(SUM(s.sale_price) FILTER (WHERE s.is_phone), 0), 2) AS "手机销售额",
                           COUNT(*) FILTER (WHERE s.product_type = '回收') AS "回收台数",
                           MAX(s.sale_date) AS "最新日期"
                    FROM fact_store_sales s JOIN dim_store d ON d.store_id = s.store_id
                    WHERE s.month_label IN ({months}){store_filter}
                    GROUP BY d.short_name, d.store_id, s.month_label
                    ORDER BY d.store_id, s.month_label
                """,
            },
            {
                "id": "store_targets",
                "purpose": "门店毛利目标",
                "sql": f"""
                    SELECT d.short_name AS "门店", t.target_value AS "毛利目标", t.achieved_value AS "台账达成",
                           t.as_of AS "统计截至"
                    FROM fact_store_targets t JOIN dim_store d ON d.store_id = t.store_id
                    WHERE t.month_label IN ({current}) AND t.owner_type = 'store' AND t.item = '总毛利'{target_filter}
                    ORDER BY d.store_id
                """,
            },
            {
                "id": "type_mix",
                "purpose": "产品线销售与毛利",
                "sql": f"""
                    SELECT COALESCE(s.product_type, '未分类') AS "产品线", s.month_label AS "月份",
                           ROUND(COALESCE(SUM(s.sale_price), 0), 2) AS "销售额",
                           ROUND(COALESCE(SUM(s.gross_profit), 0), 2) AS "毛利",
                           COUNT(*) AS "件数"
                    FROM fact_store_sales s
                    WHERE s.month_label IN ({months}){store_filter}
                    GROUP BY COALESCE(s.product_type, '未分类'), s.month_label
                    ORDER BY "毛利" DESC
                """,
            },
            {
                "id": "item_targets",
                "purpose": "产品线毛利目标与完成",
                "sql": f"""
                    SELECT d.short_name AS "门店", t.item AS "考核项",
                           t.target_value AS "目标", t.achieved_value AS "完成",
                           ROUND(t.achieved_value / NULLIF(t.target_value, 0), 4) AS "达成率",
                           t.target_value - COALESCE(t.achieved_value, 0) AS "差额"
                    FROM fact_store_targets t JOIN dim_store d ON d.store_id = t.store_id
                    WHERE t.month_label IN ({current}) AND t.owner_type = 'store' AND t.unit = '毛利'
                      AND t.item <> '总毛利' AND t.target_value > 0{target_filter}
                    ORDER BY "差额" DESC
                """,
            },
            {
                "id": "staff",
                "purpose": "店员业绩",
                "sql": f"""
                    WITH perf AS (
                        SELECT s.store_id, s.salesperson,
                               SUM(s.sale_price) AS sales, SUM(s.gross_profit) AS profit,
                               COUNT(*) FILTER (WHERE s.is_phone) AS phones,
                               SUM(COALESCE(s.commission, 0) + COALESCE(s.red_packet, 0)) AS bonus
                        FROM fact_store_sales s
                        WHERE s.month_label IN ({current}) AND s.salesperson IS NOT NULL{store_filter}
                        GROUP BY s.store_id, s.salesperson
                    )
                    SELECT d.short_name AS "门店", p.salesperson AS "店员",
                           ROUND(p.sales, 2) AS "销售额", ROUND(p.profit, 2) AS "毛利", p.phones AS "手机台数",
                           ROUND(p.bonus, 2) AS "提成红包", t.target_value AS "个人毛利目标",
                           ROUND(p.profit / NULLIF(t.target_value, 0), 4) AS "达成率"
                    FROM perf p
                    JOIN dim_store d ON d.store_id = p.store_id
                    LEFT JOIN fact_store_targets t
                      ON t.store_id = p.store_id AND t.owner = p.salesperson AND t.owner_type = 'staff'
                     AND t.item = '总毛利' AND t.month_label IN ({current})
                    ORDER BY p.profit DESC NULLS LAST
                """,
            },
            {
                "id": "payment_mix",
                "purpose": "收款方式结构",
                "sql": f"""
                    SELECT COALESCE(s.payment_method, '未填写') AS "收款方式", COUNT(*) AS "笔数",
                           ROUND(SUM(s.sale_price), 2) AS "销售额",
                           ROUND(SUM(s.subsidy_receivable), 2) AS "国补应返"
                    FROM fact_store_sales s
                    WHERE s.month_label IN ({current}){store_filter}
                    GROUP BY COALESCE(s.payment_method, '未填写')
                    ORDER BY "销售额" DESC NULLS LAST
                """,
            },
            {
                "id": "stock_age",
                "purpose": "门店库龄",
                "sql": f"""
                    SELECT d.short_name AS "门店", SUM(a.quantity) AS "在库数量",
                           ROUND(SUM(a.cost_price * a.quantity), 2) AS "库存金额",
                           SUM(a.quantity) FILTER (WHERE a.age_days > {LONG_STOCK_DAYS}) AS "长库龄数量",
                           ROUND(SUM(a.cost_price * a.quantity) FILTER (WHERE a.age_days > {LONG_STOCK_DAYS}), 2) AS "长库龄金额",
                           ROUND(AVG(a.age_days), 0) AS "平均库龄"
                    FROM fact_stock_age a JOIN dim_store d ON d.store_id = a.store_id
                    WHERE a.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_stock_age){stock_filter}
                    GROUP BY d.short_name, d.store_id
                    ORDER BY d.store_id
                """,
            },
            {
                "id": "daily_trend",
                "purpose": "每日销售额与毛利",
                "sql": f"""
                    SELECT s.sale_date AS "日期", ROUND(SUM(s.sale_price), 2) AS "销售额",
                           ROUND(SUM(s.gross_profit), 2) AS "毛利"
                    FROM fact_store_sales s
                    WHERE s.month_label IN ({current}){store_filter}
                    GROUP BY s.sale_date
                    ORDER BY s.sale_date
                """,
            },
        ]
        return f"生成 {len(state['sql_queries'])} 条 PostgreSQL 分析 SQL。"

    return run_node(state, "sql", work)


def executor_node(state: AgentState) -> AgentState:
    def work() -> str:
        state["sql_queries"] = execute_queries(state["sql_queries"])
        return f"执行 {len(state['sql_queries'])} 条只读查询。"

    return run_node(state, "executor", work)


def analysis_node(state: AgentState) -> AgentState:
    def work() -> str:
        period = state["period"]
        overview = rows_of(_query(state, "store_overview"))
        current_rows = [r for r in overview if r["月份"] == period["current"]]
        previous_rows = [r for r in overview if r["月份"] == period["previous"]] if period["previous"] != period["current"] else []
        # 环比只比较两个月都有数据的门店（同店口径），避免新接入的门店把增幅放大
        same_store = {r["门店"] for r in previous_rows} & {r["门店"] for r in current_rows}
        previous_rows = [r for r in previous_rows if r["门店"] in same_store]
        period["same_store"] = sorted(same_store)
        if not current_rows:
            raise ValueError(f"{period['current']} 没有门店销售数据，无法分析。")

        targets = {r["门店"]: float(r["毛利目标"] or 0) for r in rows_of(_query(state, "store_targets"))}
        progress = _month_progress(period)

        def total(rows: list[dict[str, Any]], key: str) -> float:
            return sum(float(r.get(key) or 0) for r in rows)

        cur = {k: total(current_rows, k) for k in ("销售额", "毛利", "手机台数")}
        cur_same = {k: total([r for r in current_rows if r["门店"] in same_store], k) for k in ("销售额", "毛利", "手机台数")}
        cur_same["毛利率"] = cur_same["毛利"] / cur_same["销售额"] if cur_same["销售额"] else 0
        prev = {k: total(previous_rows, k) for k in ("销售额", "毛利", "手机台数")}
        cur["毛利率"] = cur["毛利"] / cur["销售额"] if cur["销售额"] else 0
        prev["毛利率"] = prev["毛利"] / prev["销售额"] if prev["销售额"] else 0
        target_total = sum(targets.get(r["门店"], 0) for r in current_rows)

        metrics = []
        comparison = "环比" if not same_store or len(same_store) == len(current_rows) else f"同店环比（{'、'.join(sorted(same_store))}）"
        for name in ("销售额", "毛利", "毛利率", "手机台数"):
            change = cur_same[name] - prev[name]
            metrics.append({
                "name": name,
                "previous": prev[name] if previous_rows else None,
                "current": cur[name],
                "change": change if previous_rows else None,
                "change_rate": safe_rate(change, prev[name]) if previous_rows else None,
                "comparison": comparison,
            })
        if target_total:
            metrics.append({"name": "毛利目标", "previous": None, "current": target_total, "change": None, "change_rate": None})
            metrics.append({"name": "毛利达成率", "previous": None, "current": cur["毛利"] / target_total,
                            "change": None, "change_rate": None})
            if progress < 1:
                metrics.append({"name": "预计月底达成率", "previous": None,
                                "current": cur["毛利"] / progress / target_total, "change": None, "change_rate": None})
        state["metrics"] = metrics
        return f"计算 {len(metrics)} 个核心指标（月度进度 {progress:.0%}）。"

    return run_node(state, "analysis", work)


def attribution_node(state: AgentState) -> AgentState:
    def work() -> str:
        period = state["period"]
        progress = _month_progress(period)
        overview = rows_of(_query(state, "store_overview"))
        targets = {r["门店"]: r for r in rows_of(_query(state, "store_targets"))}
        previous = {r["门店"]: r for r in overview if r["月份"] == period["previous"]} if period["previous"] != period["current"] else {}

        stores = []
        for row in (r for r in overview if r["月份"] == period["current"]):
            target = float((targets.get(row["门店"]) or {}).get("毛利目标") or 0)
            profit = float(row["毛利"] or 0)
            prev_profit = previous.get(row["门店"], {}).get("毛利")
            stores.append({
                "门店": row["门店"],
                "销售额": float(row["销售额"] or 0),
                "毛利": profit,
                "毛利率": float(row["毛利率"] or 0),
                "手机台数": int(row["手机台数"] or 0),
                "手机销售额": float(row["手机销售额"] or 0),
                "手机销售占比": float(row["手机销售额"] or 0) / float(row["销售额"]) if row["销售额"] else None,
                "手机均价": float(row["手机销售额"] or 0) / int(row["手机台数"]) if row["手机台数"] else None,
                "毛利目标": target or None,
                "达成率": profit / target if target else None,
                "预计达成率": profit / progress / target if target and progress < 1 else None,
                "毛利环比": safe_rate(profit - float(prev_profit), float(prev_profit)) if prev_profit else None,
            })
        stores.sort(key=lambda r: (r["达成率"] is None, r["达成率"] or 0))

        type_rows = [r for r in rows_of(_query(state, "type_mix")) if r["月份"] == period["current"]]
        profit_total = sum(float(r["毛利"] or 0) for r in type_rows) or 1
        type_rows.sort(key=lambda r: -float(r["毛利"] or 0))
        types = [{
            "产品线": r["产品线"], "销售额": float(r["销售额"] or 0), "毛利": float(r["毛利"] or 0),
            "件数": int(r["件数"] or 0), "毛利占比": float(r["毛利"] or 0) / profit_total,
        } for r in type_rows]

        gaps = [{
            "门店": r["门店"], "考核项": r["考核项"], "目标": float(r["目标"] or 0), "完成": float(r["完成"] or 0),
            "达成率": float(r["达成率"]) if r["达成率"] is not None else None, "差额": float(r["差额"] or 0),
        } for r in rows_of(_query(state, "item_targets")) if float(r["差额"] or 0) > 0][:8]

        staff = rows_of(_query(state, "staff"))
        stock = rows_of(_query(state, "stock_age"))
        payments = rows_of(_query(state, "payment_mix"))
        state["attribution"] = {
            "stores": stores,
            "types": types,
            "gaps": gaps,
            "staff": staff,
            "stock": stock,
            "payments": payments,
            "tables": [
                {"title": "门店达成", "rows": stores},
                {"title": "产品线差距最大（毛利目标）", "rows": gaps},
                {"title": "店员业绩", "rows": staff[:10]},
                {"title": "产品线结构", "rows": types[:10]},
                {"title": f"库龄（超过 {LONG_STOCK_DAYS} 天为长库龄）", "rows": stock},
                {"title": "收款方式", "rows": payments[:8]},
            ],
        }
        return f"完成 {len(stores)} 家门店达成、{len(types)} 条产品线、{len(staff)} 名店员、库龄与收款结构分析。"

    return run_node(state, "attribution", work)


def visualization_node(state: AgentState) -> AgentState:
    def work() -> str:
        attr = state["attribution"]
        stores = attr["stores"]
        types = [t for t in attr["types"] if t["产品线"] != "未分类"][:10]
        daily = rows_of(_query(state, "daily_trend"))
        charts = [
            {
                "title": "门店毛利与目标",
                "type": "bar",
                "echarts_option": bar_chart(
                    [s["门店"] for s in stores],
                    [
                        {"name": "毛利", "data": [round(s["毛利"], 2) for s in stores]},
                        {"name": "毛利目标", "data": [s["毛利目标"] or 0 for s in stores]},
                    ],
                ),
            },
            {
                "title": "产品线毛利",
                "type": "bar",
                "echarts_option": bar_chart([t["产品线"] for t in types],
                                            [{"name": "毛利", "data": [round(t["毛利"], 2) for t in types]}]),
            },
        ]
        if len(daily) > 1:
            charts.insert(0, {
                "title": "每日销售额与毛利",
                "type": "line",
                "echarts_option": line_chart(
                    [str(r["日期"])[5:] for r in daily],
                    [
                        {"name": "销售额", "data": [r["销售额"] for r in daily]},
                        {"name": "毛利", "data": [r["毛利"] for r in daily]},
                    ],
                ),
            })
        state["charts"] = charts
        return f"生成 {len(charts)} 个图表配置。"

    return run_node(state, "visualization", work)


def report_node(state: AgentState) -> AgentState:
    def work() -> str:
        period = state["period"]
        month = _month_text(period["current"])
        metrics = {m["name"]: m for m in state["metrics"]}
        attr = state["attribution"]
        stores = attr["stores"]
        scope = "、".join(s["short_name"] for s in state["stores"]) or f"{len(stores)} 家门店"
        progress = _month_progress(period)

        profit = metrics["毛利"]["current"]
        summary = (
            f"{month} {scope}销售额 {_wan(metrics['销售额']['current'])}、毛利 {_wan(profit)}，"
            f"毛利率 {metrics['毛利率']['current']:.1%}，手机 {int(metrics['手机台数']['current'])} 台"
        )
        if "毛利达成率" in metrics:
            summary += f"；毛利目标 {_wan(metrics['毛利目标']['current'])}，达成 {metrics['毛利达成率']['current']:.1%}"
            if "预计月底达成率" in metrics:
                summary += f"（数据截至 {period['last_day']}，按当前进度预计月底 {metrics['预计月底达成率']['current']:.1%}）"
        summary += "。"
        ranked = [s for s in stores if s["达成率"] is not None]
        if len(ranked) > 1:
            summary += f" 达成最高的是{ranked[-1]['门店']}（{ranked[-1]['达成率']:.1%}），最低的是{ranked[0]['门店']}（{ranked[0]['达成率']:.1%}）。"

        findings: list[str] = []
        focused: dict[str, str] = {}
        if metrics["毛利"]["change_rate"] is not None:
            findings.append(f"{metrics['毛利']['comparison']}：毛利 {_fmt_rate(metrics['毛利']['change_rate'])}，"
                            f"销售额 {_fmt_rate(metrics['销售额']['change_rate'])}。")
        for s in stores:
            line = f"{s['门店']}：销售额 {_wan(s['销售额'])}，毛利 {_wan(s['毛利'])}（毛利率 {s['毛利率']:.1%}），手机 {s['手机台数']} 台"
            if s["手机台数"]:
                line += (f"（手机销售额 {_wan(s['手机销售额'])}，占销售额 {s['手机销售占比']:.0%}，"
                         f"均价 {s['手机均价']:,.0f} 元）")
            if s["达成率"] is not None:
                line += f"，毛利目标 {_wan(s['毛利目标'])}，达成 {s['达成率']:.1%}"
            findings.append(line + "。")
        top_types = sorted((t for t in attr["types"] if t["产品线"] != "未分类"), key=lambda t: -t["毛利"])[:3]
        if top_types:
            focused["product"] = "毛利贡献最大的产品线：" + "、".join(
                f"{t['产品线']} {_wan(t['毛利'])}（{t['毛利占比']:.0%}）" for t in top_types) + "。"
            findings.append(focused["product"])
        losers = [t for t in attr["types"] if t["毛利"] < 0]
        if losers:
            findings.append("毛利为负的部分：" + "、".join(f"{t['产品线']} {t['毛利']:,.0f} 元" for t in losers)
                            + "（多为赠品、膜等随单商品，计入了成本但售价为 0）。")
        if attr["gaps"]:
            g = attr["gaps"][0]
            focused["target"] = (f"差距最大的考核项是{g['门店']}的{g['考核项']}：目标 {g['目标']:,.0f} 元，完成 {g['完成']:,.0f} 元，"
                                 f"还差 {g['差额']:,.0f} 元。")
            findings.append(focused["target"])
        staff = [r for r in attr["staff"] if r.get("个人毛利目标")]
        if staff:
            best = max(staff, key=lambda r: float(r["达成率"] or 0))
            worst = min(staff, key=lambda r: float(r["达成率"] or 0))
            focused["staff"] = (f"店员个人毛利达成最高的是{best['门店']}{best['店员']}（{float(best['达成率'] or 0):.0%}），"
                                f"最低的是{worst['门店']}{worst['店员']}（{float(worst['达成率'] or 0):.0%}）。")
            findings.append(focused["staff"])
        long_stock = [r for r in attr["stock"] if r.get("长库龄数量")]
        if long_stock:
            qty = sum(int(r["长库龄数量"] or 0) for r in long_stock)
            amount = sum(float(r["长库龄金额"] or 0) for r in long_stock)
            worst_stock = max(long_stock, key=lambda r: float(r["长库龄金额"] or 0))
            focused["stock"] = (f"库龄超过 {LONG_STOCK_DAYS} 天的库存共 {qty} 件、{_wan(amount)}，"
                                f"其中{worst_stock['门店']}最多（{_wan(float(worst_stock['长库龄金额'] or 0))}）。")
            findings.append(focused["stock"])
        if attr["payments"]:
            p = attr["payments"][0]
            share = float(p["销售额"] or 0) / (metrics["销售额"]["current"] or 1)
            findings.append(f"收款以{p['收款方式']}为主，占销售额 {share:.0%}。")

        if state["focus"] in focused:  # 用户关心的方面排在最前面
            findings.remove(focused[state["focus"]])
            findings.insert(0, focused[state["focus"]])
        state["report"] = {
            "summary": summary,
            "findings": findings,
            "recommendations": _recommendations(state["focus"], stores, attr, progress),
            "markdown": "",
        }
        state["report"]["markdown"] = render_markdown("门店经营分析报告", summary, findings, state["report"]["recommendations"])
        return "生成结构化报告和业务建议。"

    return run_node(state, "report", work)


_NODES = (
    planner_node,
    knowledge_node,
    sql_node,
    executor_node,
    analysis_node,
    attribution_node,
    visualization_node,
    report_node,
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _resolve_period(question: str) -> tuple[str, str, str]:
    """
    确定分析月份：问题中显式提到的月份优先（"2026-08"、"2026年8月"、"8月"），否则取有销售数据的最新月份；
    上期为数据中紧邻的前一个月份（没有则与本期相同，不做环比）。返回 (本期, 上期, 本期数据最新日期)。
    """
    result = execute_sql("""
        SELECT month_label, MAX(sale_date)::text FROM fact_store_sales GROUP BY month_label ORDER BY month_label
    """)
    months = {row[0]: row[1] for row in result["data"]}
    if not months:
        raise ValueError("fact_store_sales 中没有销售数据，请先导入门店台账（python -m backend.store_data.loader）。")
    labels = list(months)
    current = labels[-1]
    match = re.search(r"(\d{4})\s*[-年/.]\s*(\d{1,2})", question)
    if match:
        wanted = f"{match.group(1)}-{int(match.group(2)):02d}"
    else:
        match = re.search(r"(?<!\d)(\d{1,2})\s*月", question)
        wanted = f"{current[:4]}-{int(match.group(1)):02d}" if match else None
    if wanted in months:
        current = wanted
    idx = labels.index(current)
    previous = labels[idx - 1] if idx > 0 else current
    return current, previous, months[current]


def _resolve_stores(question: str) -> list[dict[str, Any]]:
    """问题里提到的门店（简称、商场名、别名均可）；没提到表示全部门店。"""
    result = execute_sql("SELECT store_id, short_name, store_name, mall, aliases FROM dim_store ORDER BY store_id")
    found = []
    for store_id, short, name, mall, aliases in result["data"]:
        names = [short, name, mall, *(aliases or [])]
        if any(n and n in question for n in names):
            found.append({"store_id": store_id, "short_name": short})
    return found


def _months_in_clause(period: dict[str, str]) -> str:
    months = {period["previous"], period["current"]}
    if not all(_MONTH_LABEL.match(m) for m in months):
        raise ValueError(f"非法的月份标签: {months}")
    return ", ".join(f"'{m}'" for m in sorted(months))


def _month_progress(period: dict[str, str]) -> float:
    """本期数据覆盖了当月多少天（用于月中预估月底达成）。"""
    year, month = (int(x) for x in period["current"].split("-"))
    days = calendar.monthrange(year, month)[1]
    last_day = int(str(period.get("last_day") or "")[8:10] or days)
    return min(last_day / days, 1.0)


def _month_text(label: str) -> str:
    year, month = label.split("-")
    return f"{year} 年 {int(month)} 月"


def _query(state: AgentState, query_id: str) -> dict[str, Any]:
    return next(item for item in state["sql_queries"] if item["id"] == query_id)


def _wan(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 10000:,.2f} 万元"


def _fmt_rate(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:+.2%}"


def _recommendations(focus: str, stores: list[dict[str, Any]], attr: dict[str, Any], progress: float) -> list[str]:
    tips: list[str] = []
    behind = [s for s in stores if s["达成率"] is not None and (s["预计达成率"] or s["达成率"]) < 1]
    gaps = attr["gaps"]
    long_stock = sorted((r for r in attr["stock"] if r.get("长库龄金额")), key=lambda r: -float(r["长库龄金额"] or 0))
    staff_behind = [r for r in attr["staff"] if r.get("达成率") is not None and float(r["达成率"]) < 0.6]

    if behind:
        worst = behind[0]
        store_gaps = [g for g in gaps if g["门店"] == worst["门店"]][:2]
        gap_text = "、".join(f"{g['考核项']}（差 {g['差额']:,.0f} 元）" for g in store_gaps)
        tips.append(f"{worst['门店']}毛利达成 {worst['达成率']:.0%}，差距最大，"
                    + (f"优先补{gap_text}，" if gap_text else "")
                    + "对照本月门店指导书的产品政策，主推进店毛利高、有 SO 激励的机型。")
    if focus == "stock" or long_stock:
        if long_stock:
            r = long_stock[0]
            tips.append(f"{r['门店']}长库龄库存 {int(r['长库龄数量'] or 0)} 件、{_wan(float(r['长库龄金额'] or 0))}，"
                        "按清尾明细的分值规则安排清尾，演示机和融合类商品优先。")
    if focus == "staff" or staff_behind:
        if staff_behind:
            names = "、".join(f"{r['门店']}{r['店员']}" for r in staff_behind[:3])
            tips.append(f"{names}个人毛利达成不足 60%，复盘成交结构，提高增值业务（碎屏宝、延长保）、膜和旧机回收的连带率。")
    if not tips or len(tips) < 3:
        tips.append("每周用同一口径（销售额、毛利、手机台数、毛利达成率）复盘各门店，月中按进度预估月底达成，提前调整主推机型。")
    if progress < 1 and len(tips) < 3:
        tips.append(f"当前数据只覆盖本月 {progress:.0%} 的天数，预计达成率按日均推算，月底前请以最新台账复核。")
    return tips[:4]



def _html_report(detail: dict[str, Any], markdown: str) -> str:
    def esc(value: Any) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def rate(value: Any) -> str:
        return "—" if value is None else f"{float(value) * 100:.2f}%"

    metric_rows = "\n".join(
        "<tr>"
        f"<td>{esc(item.get('name'))}</td>"
        f"<td>{esc(round(float(item.get('previous') or 0), 4))}</td>"
        f"<td>{esc(round(float(item.get('current') or 0), 4))}</td>"
        f"<td>{esc(rate(item.get('change_rate')))}</td>"
        "</tr>"
        for item in detail.get("metrics", [])
    )
    sql_blocks = "\n".join(
        f"<details><summary>{esc(item.get('purpose'))}</summary><pre>{esc(item.get('sql'))}</pre></details>"
        for item in detail.get("sql_queries", [])
    )

    # 简单的 Markdown → HTML：标题、段落、列表（连续的 "- " 行合并进同一个 <ul>）
    body: list[str] = []
    in_list = False
    for line in markdown.splitlines():
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{esc(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("# "):
            continue  # 一级标题由模板输出
        if line.startswith("#"):
            body.append(f"<h2>{esc(line.lstrip('# ').strip())}</h2>")
        elif line.strip():
            body.append(f"<p>{esc(line)}</p>")
    if in_list:
        body.append("</ul>")
    paragraphs = "\n".join(body)
    title = esc(markdown.splitlines()[0].lstrip("# ").strip()) if markdown else "分析报告"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #1f2937; }}
    h1, h2 {{ color: #111827; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; }}
    th {{ background: #f3f4f6; }}
    pre {{ white-space: pre-wrap; background: #111827; color: #e5e7eb; padding: 12px; border-radius: 8px; }}
    details {{ margin: 10px 0; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p><strong>问题：</strong>{esc(detail.get("question", ""))}</p>
  <p><strong>Run ID：</strong>{esc(detail.get("run_id", ""))}</p>
  {paragraphs}
  <h2>核心指标</h2>
  <table>
    <thead><tr><th>指标</th><th>上期</th><th>本期</th><th>环比</th></tr></thead>
    <tbody>{metric_rows}</tbody>
  </table>
  <h2>SQL 执行记录</h2>
  {sql_blocks}
</body>
</html>"""
