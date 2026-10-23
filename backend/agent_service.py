"""
经营分析 Agent — 基于 LangGraph 的确定性分析工作流

  planner → knowledge → sql → executor → analysis → attribution → visualization → report

设计取舍:
  - 不依赖 LLM：SQL 由模板生成，结论可复现、可审计，适合作为"可信基线"
  - 分析周期从数据中自动识别（最新月份 vs 上一月份），也支持问题中显式指定月份
  - 任何节点失败后，下游节点自动跳过，错误定位到具体步骤
"""

from __future__ import annotations

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

# 有效订单 + 按订单预聚合的退款，避免一单多笔退款时 JOIN 放大 GMV
_ORDERS_WITH_REFUNDS = """
    SELECT o.*, COALESCE(rf.refund_amount, 0) AS refund_amount
    FROM fact_orders o
    LEFT JOIN (
        SELECT order_id, SUM(refund_amount) AS refund_amount
        FROM fact_refunds
        GROUP BY order_id
    ) rf ON rf.order_id = o.order_id
    WHERE o.status = 'paid'
"""

_FOCUS_KEYWORDS = {
    "roi": ("ROI", "roi", "渠道", "投放", "营销"),
    "refund": ("退款", "售后", "退货"),
    "region": ("华北", "北京", "地区", "区域", "省", "高价值用户"),
}


class AgentState(TypedDict, total=False):
    question: str
    analysis_mode: str
    run_id: str
    period: dict[str, str]
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
        "经营分析报告",
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
        current, previous = _resolve_period(question)
        state["period"] = {"current": current, "previous": previous}

        focus = "overview"
        for name, words in _FOCUS_KEYWORDS.items():
            if any(word in question for word in words):
                focus = name
                break
        state["focus"] = focus

        plan = [
            {"id": "monthly_trend", "purpose": f"对比 {previous} 与 {current} 核心经营指标"},
            {"id": "region_attribution", "purpose": "按省份定位 GMV 变化来源"},
            {"id": "category_attribution", "purpose": "按品类拆解销售、成本和退款"},
            {"id": "channel_roi", "purpose": "按渠道计算净销售额、营销花费和 ROI"},
            {"id": "traffic_conversion", "purpose": "检查渠道和大区转化率变化"},
        ]
        focus_purpose = {
            "roi": "重点解释 ROI 变化最大的渠道",
            "refund": "重点解释退款率最高的品类",
            "region": "重点解释拖累最大的地区及其转化漏斗",
        }.get(focus)
        if focus_purpose:
            plan.append({"id": f"{focus}_focus", "purpose": focus_purpose})
        state["plan"] = plan
        return f"分析周期 {previous} → {current}，关注点 {focus}，生成 {len(plan)} 个分析步骤。"

    return run_node(state, "planner", work)


def knowledge_node(state: AgentState) -> AgentState:
    def work() -> str:
        # 优先检索企业知识库（客户自己的指标口径/业务规则），知识库为空或不可用时回退到内置演示知识
        try:
            from backend.kb.rag import knowledge_for_agent

            state["knowledge"] = knowledge_for_agent(state["question"])
        except Exception as exc:
            logger.warning("企业知识库检索失败，回退到内置知识: %s", exc)
            state["knowledge"] = []
        if state["knowledge"]:
            return f"从企业知识库检索到 {len(state['knowledge'])} 条相关知识。"

        candidates = ("GMV", "销售额", "净销售额", "毛利", "退款", "ROI", "渠道", "华北", "北京", "高价值用户", "销售下降", "利润")
        keywords = [kw for kw in candidates if kw in state["question"]] or ["GMV", "销售下降", "ROI", "退款"]
        clauses = " OR ".join(f"keywords ILIKE :kw{i}" for i in range(len(keywords)))
        params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(keywords)}
        result = execute_sql(
            f"SELECT source, content FROM knowledge_chunks WHERE {clauses} ORDER BY chunk_id LIMIT 6",
            params,
        )
        state["knowledge"] = [{"source": row[0], "content": row[1]} for row in result["data"]]
        return f"检索到 {len(state['knowledge'])} 条内置业务知识。"

    return run_node(state, "knowledge", work)


def sql_node(state: AgentState) -> AgentState:
    def work() -> str:
        months = _months_in_clause(state["period"])
        state["sql_queries"] = [
            {
                "id": "monthly_trend",
                "purpose": "月度核心指标趋势",
                "sql": f"""
                    SELECT d.month_label AS "月份",
                           ROUND(SUM(o.order_amount), 2) AS "GMV",
                           COUNT(*) AS "订单数",
                           ROUND(AVG(o.order_amount), 2) AS "客单价",
                           ROUND(SUM(o.product_cost), 2) AS "商品成本",
                           ROUND(SUM(o.refund_amount), 2) AS "退款金额",
                           ROUND(SUM(o.order_amount) - SUM(o.refund_amount), 2) AS "净销售额",
                           ROUND((SUM(o.order_amount) - SUM(o.product_cost) - SUM(o.refund_amount)) / NULLIF(SUM(o.order_amount), 0), 4) AS "毛利率"
                    FROM ({_ORDERS_WITH_REFUNDS}) o
                    JOIN dim_date d ON d.date_id = o.order_date
                    WHERE d.month_label IN ({months})
                    GROUP BY d.month_label
                    ORDER BY d.month_label
                """,
            },
            {
                "id": "region_attribution",
                "purpose": "地区 GMV 变化贡献",
                "sql": f"""
                    SELECT r.region_name AS "大区", r.province AS "省份", d.month_label AS "月份",
                           ROUND(SUM(o.order_amount), 2) AS "GMV"
                    FROM ({_ORDERS_WITH_REFUNDS}) o
                    JOIN dim_date d ON d.date_id = o.order_date
                    JOIN dim_region r ON r.region_id = o.region_id
                    WHERE d.month_label IN ({months})
                    GROUP BY r.region_name, r.province, d.month_label
                    ORDER BY r.region_name, r.province, d.month_label
                """,
            },
            {
                "id": "category_attribution",
                "purpose": "品类销售、成本、退款拆解",
                "sql": f"""
                    SELECT p.category AS "品类", d.month_label AS "月份",
                           ROUND(SUM(o.order_amount), 2) AS "GMV",
                           ROUND(SUM(o.product_cost), 2) AS "商品成本",
                           ROUND(SUM(o.refund_amount), 2) AS "退款金额",
                           ROUND(SUM(o.refund_amount) / NULLIF(SUM(o.order_amount), 0), 4) AS "退款率"
                    FROM ({_ORDERS_WITH_REFUNDS}) o
                    JOIN dim_date d ON d.date_id = o.order_date
                    JOIN dim_product p ON p.product_id = o.product_id
                    WHERE d.month_label IN ({months})
                    GROUP BY p.category, d.month_label
                    ORDER BY p.category, d.month_label
                """,
            },
            {
                "id": "channel_roi",
                "purpose": "渠道 ROI 变化",
                "sql": f"""
                    WITH sales AS (
                        SELECT o.channel_id, d.month_label,
                               SUM(o.order_amount) AS gmv,
                               SUM(o.refund_amount) AS refunds
                        FROM ({_ORDERS_WITH_REFUNDS}) o
                        JOIN dim_date d ON d.date_id = o.order_date
                        WHERE d.month_label IN ({months})
                        GROUP BY o.channel_id, d.month_label
                    ),
                    spend AS (
                        SELECT ms.channel_id, d.month_label, SUM(ms.spend_amount) AS spend
                        FROM fact_marketing_spend ms
                        JOIN dim_date d ON d.date_id = ms.spend_date
                        WHERE d.month_label IN ({months})
                        GROUP BY ms.channel_id, d.month_label
                    )
                    SELECT c.channel_name AS "渠道", s.month_label AS "月份",
                           ROUND(s.gmv - s.refunds, 2) AS "净销售额",
                           ROUND(sp.spend, 2) AS "营销花费",
                           ROUND((s.gmv - s.refunds) / NULLIF(sp.spend, 0), 4) AS "ROI"
                    FROM sales s
                    JOIN spend sp ON sp.channel_id = s.channel_id AND sp.month_label = s.month_label
                    JOIN dim_channel c ON c.channel_id = s.channel_id
                    ORDER BY c.channel_name, s.month_label
                """,
            },
            {
                "id": "traffic_conversion",
                "purpose": "渠道转化率变化",
                "sql": f"""
                    SELECT c.channel_name AS "渠道", r.region_name AS "大区", d.month_label AS "月份",
                           SUM(t.visits) AS "访问量",
                           SUM(t.paid_orders) AS "支付订单数",
                           ROUND(SUM(t.paid_orders)::numeric / NULLIF(SUM(t.visits), 0), 4) AS "转化率"
                    FROM fact_daily_traffic t
                    JOIN dim_date d ON d.date_id = t.traffic_date
                    JOIN dim_channel c ON c.channel_id = t.channel_id
                    JOIN dim_region r ON r.region_id = t.region_id
                    WHERE d.month_label IN ({months})
                    GROUP BY c.channel_name, r.region_name, d.month_label
                    ORDER BY c.channel_name, r.region_name, d.month_label
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
        by_month = {row["月份"]: row for row in rows_of(_query(state, "monthly_trend"))}
        previous = by_month.get(period["previous"], {})
        current = by_month.get(period["current"], {})
        if not current:
            raise ValueError(f"{period['current']} 没有订单数据，无法分析。")
        metrics = []
        for name in ("GMV", "订单数", "客单价", "商品成本", "退款金额", "净销售额", "毛利率"):
            prev = float(previous.get(name) or 0)
            cur = float(current.get(name) or 0)
            change = cur - prev
            metrics.append({
                "name": name,
                "previous": prev,
                "current": cur,
                "change": change,
                "change_rate": safe_rate(change, prev),
            })
        state["metrics"] = metrics
        return f"计算 {len(metrics)} 个核心指标。"

    return run_node(state, "analysis", work)


def attribution_node(state: AgentState) -> AgentState:
    def work() -> str:
        period = state["period"]
        region = _contribution(rows_of(_query(state, "region_attribution")), "省份", "GMV", period)
        category_rows = rows_of(_query(state, "category_attribution"))
        traffic_rows = rows_of(_query(state, "traffic_conversion"))

        # 转化率下钻到"拖累最大的省份"所在大区，而不是写死华北
        region_lookup = {row["省份"]: row["大区"] for row in rows_of(_query(state, "region_attribution"))}
        focus_region = region_lookup.get(region[0]["省份"]) if region else None

        state["attribution"] = {
            "region": region,
            "category": _contribution(category_rows, "品类", "GMV", period),
            "refund": _change(category_rows, "品类", "退款率", period, by_current=True),
            "roi": _change(rows_of(_query(state, "channel_roi")), "渠道", "ROI", period),
            "conversion_region": _change(
                [row for row in traffic_rows if row.get("大区") == focus_region],
                "渠道",
                "转化率",
                period,
            ),
            "focus_region": focus_region,
        }
        return f"完成地区、品类、退款率、ROI 归因，并下钻 {focus_region or '重点大区'} 转化率。"

    return run_node(state, "attribution", work)


def visualization_node(state: AgentState) -> AgentState:
    def work() -> str:
        monthly = rows_of(_query(state, "monthly_trend"))
        region = state["attribution"]["region"][:8]
        roi = state["attribution"]["roi"]
        state["charts"] = [
            {
                "title": "月度 GMV 与净销售额",
                "type": "line",
                "echarts_option": line_chart(
                    [row["月份"] for row in monthly],
                    [
                        {"name": "GMV", "data": [row["GMV"] for row in monthly]},
                        {"name": "净销售额", "data": [row["净销售额"] for row in monthly]},
                    ],
                ),
            },
            {
                "title": "省份 GMV 变化贡献（Top 8 下降）",
                "type": "bar",
                "echarts_option": bar_chart(
                    [row["省份"] for row in region],
                    [{"name": "GMV变化", "data": [round(row["change"], 2) for row in region]}],
                ),
            },
            {
                "title": "渠道 ROI 变化",
                "type": "bar",
                "echarts_option": bar_chart(
                    [row["渠道"] for row in roi],
                    [{"name": "ROI变化", "data": [round(row["change"], 4) for row in roi]}],
                ),
            },
        ]
        return f"生成 {len(state['charts'])} 个图表配置。"

    return run_node(state, "visualization", work)


def report_node(state: AgentState) -> AgentState:
    def work() -> str:
        current_month = state["period"]["current"]
        focus = state["focus"]
        metrics = {item["name"]: item for item in state["metrics"]}
        attr = state["attribution"]
        top_region = attr["region"][0] if attr["region"] else None
        top_category = attr["category"][0] if attr["category"] else None
        top_refund = attr["refund"][0] if attr["refund"] else None
        top_roi = attr["roi"][0] if attr["roi"] else None
        focus_region = attr.get("focus_region")

        if focus == "roi" and top_roi:
            summary = (
                f"{current_month} 渠道分析显示，{top_roi['渠道']} ROI 下滑最明显，"
                f"从 {top_roi['previous']:.4f} 到 {top_roi['current']:.4f}，变化 {top_roi['change']:+.4f}。"
            )
        elif focus == "refund" and top_refund:
            summary = (
                f"{current_month} 退款率最高的品类是{top_refund['品类']}，"
                f"退款率从 {top_refund['previous']:.2%} 变为 {top_refund['current']:.2%}。"
            )
        elif focus == "region" and top_region:
            summary = (
                f"{current_month} 地区分析显示，{top_region['省份']}（{focus_region or '—'}）GMV 变化最大，"
                f"从 {top_region['previous']:,.2f} 到 {top_region['current']:,.2f}，变化 {top_region['change']:+,.2f}。"
            )
        else:
            summary = (
                f"{current_month} GMV 环比 {_fmt_rate(metrics['GMV']['change_rate'])}，"
                f"净销售额环比 {_fmt_rate(metrics['净销售额']['change_rate'])}。"
            )
            if top_region and top_region["change"] < 0:
                summary += f" 最大拖累省份是{top_region['省份']}，GMV 变化 {top_region['change']:+,.2f}。"

        findings = [
            _metric_sentence("GMV", metrics["GMV"]),
            _metric_sentence("净销售额", metrics["净销售额"]),
            _metric_sentence("退款金额", metrics["退款金额"]),
            _metric_sentence("毛利率", metrics["毛利率"], percent_value=True),
        ]
        if top_category and top_category["change"] < 0:
            findings.append(
                f"{top_category['品类']}是品类侧最大下降来源，GMV 变化 {top_category['change']:+,.2f}，"
                f"占总变化 {top_category['contribution']:.2%}。"
            )
        if top_refund:
            findings.append(f"{top_refund['品类']}退款率最高，当前为 {top_refund['current']:.2%}。")
        if top_roi:
            findings.append(f"{top_roi['渠道']} ROI 变化最明显，从 {top_roi['previous']:.4f} 到 {top_roi['current']:.4f}。")
        conversion = attr.get("conversion_region") or []
        if conversion and conversion[0]["change"] < 0:
            worst = conversion[0]
            findings.append(
                f"{focus_region}{worst['渠道']}转化率从 {worst['previous']:.2%} 降至 {worst['current']:.2%}，"
                "是该区域漏斗中下滑最明显的环节。"
            )

        recommendations = _recommendations_for(focus, top_region, top_refund, top_roi, focus_region)
        state["report"] = {
            "summary": summary,
            "findings": findings,
            "recommendations": recommendations,
            "markdown": render_markdown("经营分析报告", summary, findings, recommendations),
        }
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


def _resolve_period(question: str) -> tuple[str, str]:
    """
    确定分析周期：问题中显式提到的月份优先（"2026-05"、"2026年5月"、"5月"），
    否则取有订单数据的最新月份；上期为数据中紧邻的前一个月份。
    """
    result = execute_sql(
        """
        SELECT DISTINCT d.month_label
        FROM fact_orders o JOIN dim_date d ON d.date_id = o.order_date
        ORDER BY d.month_label
        """
    )
    months = [row[0] for row in result["data"]]
    if not months:
        raise ValueError("fact_orders 中没有数据，请先初始化数据库。")

    current = months[-1]
    match = re.search(r"(\d{4})\s*[-年/.]\s*(\d{1,2})", question)
    if match:
        wanted = f"{match.group(1)}-{int(match.group(2)):02d}"
    else:
        match = re.search(r"(?<!\d)(\d{1,2})\s*月", question)
        wanted = f"{current[:4]}-{int(match.group(1)):02d}" if match else None
    if wanted in months:
        current = wanted

    idx = months.index(current)
    previous = months[idx - 1] if idx > 0 else current
    return current, previous


def _months_in_clause(period: dict[str, str]) -> str:
    months = {period["previous"], period["current"]}
    if not all(_MONTH_LABEL.match(m) for m in months):
        raise ValueError(f"非法的月份标签: {months}")
    return ", ".join(f"'{m}'" for m in sorted(months))


def _query(state: AgentState, query_id: str) -> dict[str, Any]:
    return next(item for item in state["sql_queries"] if item["id"] == query_id)


def _pivot(rows: list[dict[str, Any]], dimension: str, metric: str, period: dict[str, str]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = {}
    for row in rows:
        key = str(row[dimension])
        buckets.setdefault(key, {})[str(row["月份"])] = float(row.get(metric) or 0)
    result = []
    for key, values in buckets.items():
        previous = values.get(period["previous"], 0.0)
        current = values.get(period["current"], 0.0)
        change = current - previous
        result.append({
            dimension: key,
            "previous": previous,
            "current": current,
            "change": change,
            "change_rate": safe_rate(change, previous),
        })
    return result


def _contribution(rows: list[dict[str, Any]], dimension: str, metric: str, period: dict[str, str]) -> list[dict[str, Any]]:
    result = _pivot(rows, dimension, metric, period)
    total_change = sum(item["change"] for item in result)
    for item in result:
        item["contribution"] = item["change"] / total_change if total_change else 0
    return sorted(result, key=lambda item: item["change"])


def _change(
    rows: list[dict[str, Any]],
    dimension: str,
    metric: str,
    period: dict[str, str],
    by_current: bool = False,
) -> list[dict[str, Any]]:
    """by_current=True 按当前值从高到低排序（如退款率），否则按变化从小到大（降幅最大在前）。"""
    pivot = _pivot(rows, dimension, metric, period)
    if by_current:
        return sorted(pivot, key=lambda item: item["current"], reverse=True)
    return sorted(pivot, key=lambda item: item["change"])


def _fmt_rate(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:+.2%}"


def _metric_sentence(name: str, metric: dict[str, Any], percent_value: bool = False) -> str:
    value_format = "{:.2%}" if percent_value else "{:,.2f}"
    return (
        f"{name}从 {value_format.format(metric['previous'])} 变化到 "
        f"{value_format.format(metric['current'])}，环比 {_fmt_rate(metric['change_rate'])}。"
    )


def _recommendations_for(
    focus: str,
    top_region: dict[str, Any] | None,
    top_refund: dict[str, Any] | None,
    top_roi: dict[str, Any] | None,
    focus_region: str | None,
) -> list[str]:
    channel = top_roi["渠道"] if top_roi else "ROI 下滑渠道"
    category = top_refund["品类"] if top_refund else "高退款品类"
    province = top_region["省份"] if top_region else "重点省份"
    region = focus_region or "重点大区"
    if focus == "roi":
        return [
            f"优先复盘{channel}的投放人群、素材和落地页转化，暂停低 ROI 单元继续放量。",
            "用净销售额而不是 GMV 评估渠道效果，把退款影响纳入预算分配。",
            "保留小流量 A/B 测试，按周观察 ROI、转化率和客单价是否同步修复。",
        ]
    if focus == "refund":
        return [
            f"对{category}抽样查看退款原因，区分质量、物流、描述不符和价格保护问题。",
            "把退款率、退款金额和毛利率放到同一张周报里，避免只看 GMV。",
            "对异常 SKU 设置售后预警阈值，必要时降低投放或临时下架问题批次。",
        ]
    if focus == "region":
        return [
            f"优先检查{province}的高价值用户复购、渠道转化和热销品供给是否同时走弱。",
            f"针对{region}做分渠道漏斗复盘，定位是访问下降、转化下降还是客单价下降。",
            "对高价值用户做定向召回，不建议先进行全站大额促销。",
        ]
    return [
        f"优先复盘{province}（{region}）市场的商品供给、价格策略和投放转化漏斗。",
        f"对{category}做售后原因拆解，单独监控退款金额对净销售额和毛利率的影响。",
        f"将预算从{channel}等 ROI 下滑渠道阶段性转向 ROI 稳定的渠道，并按周复盘。",
    ]


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
