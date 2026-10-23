"""
CSV 通用分析 Agent — 对任意导入表做字段画像、意图识别、只读 SQL、图表和报告

  schema_profiler → intent → generic_sql → executor → generic_analysis → visualization → report
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from backend.agent_common import (
    bar_chart,
    execute_queries,
    line_chart,
    render_markdown,
    rows_of,
    run_node,
    safe_rate,
    save_analysis_result,
)
from backend.database import execute_sql, quote_identifier, table_exists


class GenericTableState(TypedDict, total=False):
    question: str
    table_name: str
    analysis_mode: str
    run_id: str
    steps: list[dict[str, Any]]
    plan: list[dict[str, str]]
    schema_profile: dict[str, Any]
    sql_queries: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    attribution: dict[str, Any]
    charts: list[dict[str, Any]]
    report: dict[str, Any]
    errors: list[dict[str, str]]


NUMERIC_TYPES = ("integer", "bigint", "numeric", "decimal", "double", "real", "smallint")
DATE_TYPES = ("date", "timestamp")
TEXT_TYPES = ("character varying", "text", "character", "varchar")
NULL_SQL = " OR ".join(["{col} IS NULL", "NULLIF(TRIM({col}::text), '') IS NULL"])
# 导入时自动添加的技术列，不参与业务分析
TECHNICAL_COLUMNS = {"_id", "_imported_at"}
MAX_GROUPS = 50
ANOMALY_Z_THRESHOLD = 2.0

_COUNT_WORDS = ("数量", "多少", "人数", "几个", "几条", "次数", "频次", "个数", "条数", "占比")
_PERIOD_TOKENS = ("日期", "时间", "月份", "年月", "周期")


def run_table_analysis(question: str, table_name: str, analysis_mode: str = "auto", user_id: int | None = None) -> dict[str, Any]:
    if not table_name.startswith("csv_"):
        raise PermissionError("只允许分析 csv_ 前缀的导入表。")
    if not table_exists(table_name):
        raise ValueError(f"表 {table_name} 不存在。")

    state: GenericTableState = {
        "question": question.strip(),
        "table_name": table_name,
        "analysis_mode": analysis_mode,
        "run_id": str(uuid.uuid4()),
        "steps": [],
        "plan": [],
        "schema_profile": {},
        "sql_queries": [],
        "metrics": [],
        "attribution": {},
        "charts": [],
        "report": {},
        "errors": [],
    }
    for node in (
        schema_profiler_node,
        intent_node,
        generic_sql_node,
        generic_executor_node,
        generic_analysis_node,
        generic_visualization_node,
        generic_report_node,
    ):
        state = node(state)

    response = {
        "question": state["question"],
        "table_name": state["table_name"],
        "run_id": state["run_id"],
        "status": "failed" if state.get("errors") else "completed",
        "steps": state.get("steps", []),
        "plan": state.get("plan", []),
        "knowledge": [
            {
                "source": "schema_profile",
                "content": _profile_summary(state.get("schema_profile", {})),
            }
        ],
        "sql_queries": state.get("sql_queries", []),
        "metrics": state.get("metrics", []),
        "attribution": state.get("attribution", {}),
        "charts": state.get("charts", []),
        "report": state.get("report", {}),
        "errors": state.get("errors", []),
    }
    save_analysis_result(response, user_id)
    return response


def schema_profiler_node(state: GenericTableState) -> GenericTableState:
    def work() -> str:
        table = state["table_name"]
        table_q = quote_identifier(table)
        columns = _load_columns(table)
        if not columns:
            raise ValueError(f"表 {table} 没有可分析的业务字段。")
        sample = execute_sql(f"SELECT * FROM {table_q} LIMIT 20")

        # 一条 SQL 统计全部字段的缺失数和去重数，替代逐列 2N 次查询
        stat_exprs = ["COUNT(*) AS row_count"]
        for i, col in enumerate(columns):
            col_q = quote_identifier(col["name"])
            stat_exprs.append(f"COUNT(*) FILTER (WHERE {NULL_SQL.format(col=col_q)}) AS null_{i}")
            stat_exprs.append(f"COUNT(DISTINCT {col_q}) AS distinct_{i}")
        stats = execute_sql(f"SELECT {', '.join(stat_exprs)} FROM {table_q}")
        stat_row = dict(zip(stats["columns"], stats["data"][0]))
        row_count = int(stat_row["row_count"] or 0)

        for i, col in enumerate(columns):
            col["null_count"] = int(stat_row[f"null_{i}"] or 0)
            col["distinct_count"] = int(stat_row[f"distinct_{i}"] or 0)
            col["role"] = _role_for(col, row_count)
            col["semantic_type"] = _semantic_for(col)
        state["schema_profile"] = {
            "table_name": table,
            "row_count": row_count,
            "columns": columns,
            "sample": sample,
        }
        return f"读取 {len(columns)} 个字段、{row_count} 行数据的字段统计和样本。"

    return run_node(state, "schema_profiler", work)


def intent_node(state: GenericTableState) -> GenericTableState:
    def work() -> str:
        question = state["question"]
        profile = state["schema_profile"]
        date_col = _pick_column(profile, ("date", "period"), question)
        metric_col = _pick_column(profile, ("numeric",), question)
        dim_col = _pick_dimension(profile, question)
        # 用户问"数量/多少/人数"且没有点名某个数值列时，按记录数统计，而不是对某个数值列求和
        count_mode = any(w in question for w in _COUNT_WORDS) and not _mentions_column(profile, question, ("numeric",))
        aggregate = "count" if count_mode or not metric_col else "sum"

        if any(word in question for word in ("缺失", "空值", "为空", "完整")):
            intent = "missing"
        elif any(word in question for word in ("异常", "离群", "极端")):
            intent = "anomaly" if metric_col else "missing"
        elif date_col and any(word in question for word in ("趋势", "月份", "每月", "按月", "变化", "下降", "上升", "增长", "为什么")):
            intent = "trend"
        elif dim_col and any(word in question for word in ("哪个", "哪些", "最多", "最少", "最高", "最低", "排名", "类别", "分类", "分组", "分布", "占比", "为什么", "各")):
            intent = "group_compare"
        elif metric_col and not count_mode:
            intent = "distribution"
        else:
            intent = "profile"

        state["plan"] = [
            {"id": "schema_profile", "purpose": "识别表结构、字段角色和样本数据"},
            {"id": intent, "purpose": _intent_label(intent)},
        ]
        profile["selected"] = {
            "intent": intent,
            "aggregate": aggregate,
            "date_column": date_col,
            "date_is_typed": bool(date_col and _column(profile, date_col)["role"] == "date"),
            "metric_column": metric_col,
            "dimension_column": dim_col,
        }
        agg_label = "记录数" if aggregate == "count" else f"SUM({metric_col})"
        return (
            f"识别为 {intent} 分析，统计口径 {agg_label}，"
            f"日期={date_col or '无'}、指标={metric_col or '无'}、维度={dim_col or '无'}。"
        )

    return run_node(state, "intent", work)


def generic_sql_node(state: GenericTableState) -> GenericTableState:
    def work() -> str:
        table_q = quote_identifier(state["table_name"])
        profile = state["schema_profile"]
        selected = profile["selected"]
        intent = selected["intent"]
        metric = selected["metric_column"] if selected["aggregate"] == "sum" else None
        dim = selected["dimension_column"]
        queries = [_overview_query(table_q)]
        if intent == "missing":
            queries.append(_missing_query(table_q, profile["columns"]))
        elif intent == "trend":
            queries.append(_trend_query(table_q, selected["date_column"], selected["date_is_typed"], metric))
            if dim:
                queries.append(_group_query(table_q, dim, metric))
        elif intent == "group_compare":
            queries.append(_group_query(table_q, dim, metric))
        elif intent == "anomaly":
            queries.append(_anomaly_query(table_q, selected["metric_column"], _label_column(profile)))
            queries.append(_missing_query(table_q, profile["columns"]))
        elif intent == "distribution":
            queries.append(_distribution_query(table_q, selected["metric_column"]))
        else:
            queries.append(_missing_query(table_q, profile["columns"]))
            if dim:
                queries.append(_group_query(table_q, dim, None))
        state["sql_queries"] = queries
        return f"生成 {len(queries)} 条面向导入表的只读 SQL。"

    return run_node(state, "generic_sql", work)


def generic_executor_node(state: GenericTableState) -> GenericTableState:
    def work() -> str:
        state["sql_queries"] = execute_queries(state["sql_queries"])
        return f"执行 {len(state['sql_queries'])} 条导入表查询。"

    return run_node(state, "executor", work)


def generic_analysis_node(state: GenericTableState) -> GenericTableState:
    def work() -> str:
        profile = state["schema_profile"]
        selected = profile["selected"]
        row_count = profile["row_count"]
        metrics = [_snapshot_metric("行数", row_count)]
        attribution: dict[str, Any] = {}
        for query in state["sql_queries"]:
            rows = rows_of(query)
            if query["id"] == "trend" and rows:
                first = float(rows[0].get("指标值") or 0)
                last = float(rows[-1].get("指标值") or 0)
                metrics.append({
                    "name": _metric_label(selected),
                    "previous": first,
                    "current": last,
                    "change": last - first,
                    "change_rate": safe_rate(last - first, first),
                    "comparison": "首期 → 末期",
                })
                attribution["trend"] = _trend_changes(rows)
            elif query["id"] == "group_compare":
                attribution["group_compare"] = rows[:10]
                metrics.append(_snapshot_metric("分组数", len(rows)))
            elif query["id"] == "missing":
                attribution["missing"] = [row for row in rows if (row.get("缺失数") or 0) > 0][:10]
                metrics.append(_snapshot_metric("有缺失的字段数", sum(1 for row in rows if (row.get("缺失数") or 0) > 0)))
            elif query["id"] == "anomaly":
                attribution["anomaly"] = rows[:10]
                metrics.append(_snapshot_metric(f"异常值（|z|≥{ANOMALY_Z_THRESHOLD:g}）", len(rows)))
            elif query["id"] == "distribution" and rows:
                row = rows[0]
                for name in ("平均值", "中位数", "最小值", "最大值"):
                    metrics.append(_snapshot_metric(name, row.get(name) or 0))
        state["metrics"] = metrics
        state["attribution"] = attribution
        return f"计算 {len(metrics)} 个摘要指标和 {len(attribution)} 类分析结果。"

    return run_node(state, "generic_analysis", work)


def generic_visualization_node(state: GenericTableState) -> GenericTableState:
    def work() -> str:
        charts = []
        for query in state["sql_queries"]:
            rows = rows_of(query)
            if not rows:
                continue
            if query["id"] == "trend":
                charts.append({
                    "title": query["purpose"],
                    "type": "line",
                    "echarts_option": line_chart(
                        [str(row["周期"]) for row in rows],
                        [{"name": "指标值", "data": [row["指标值"] for row in rows]}],
                    ),
                })
            elif query["id"] == "group_compare":
                charts.append({
                    "title": query["purpose"],
                    "type": "bar",
                    "echarts_option": bar_chart(
                        [str(row["维度"]) for row in rows[:12]],
                        [{"name": "指标值", "data": [row["指标值"] for row in rows[:12]]}],
                    ),
                })
            elif query["id"] == "missing":
                missing_rows = [row for row in rows if (row.get("缺失数") or 0) > 0][:12]
                if missing_rows:
                    charts.append({
                        "title": "字段缺失率",
                        "type": "bar",
                        "echarts_option": bar_chart(
                            [str(row["字段"]) for row in missing_rows],
                            [{"name": "缺失率", "data": [row["缺失率"] for row in missing_rows]}],
                        ),
                    })
        state["charts"] = charts
        return f"生成 {len(charts)} 个通用图表。"

    return run_node(state, "visualization", work)


def generic_report_node(state: GenericTableState) -> GenericTableState:
    def work() -> str:
        profile = state["schema_profile"]
        selected = profile["selected"]
        intent = selected["intent"]
        attribution = state["attribution"]
        metric_label = _metric_label(selected)
        findings = []
        summary = f"已分析导入表 {state['table_name']}，共 {profile['row_count']} 行。"
        if intent == "trend" and state["metrics"][1:]:
            metric = state["metrics"][1]
            direction = "上升" if metric["change"] > 0 else "下降" if metric["change"] < 0 else "基本持平"
            summary += f" {metric_label} 从首期到末期{direction}，变化 {metric['change']:+,.2f}。"
            findings.append(f"趋势分析使用 {selected['date_column']} 作为时间列，统计口径为 {metric_label}。")
            changes = attribution.get("trend") or []
            if changes:
                worst = min(changes, key=lambda item: item["change"])
                if worst["change"] < 0:
                    findings.append(f"降幅最大的周期是 {worst['周期']}，较上一期变化 {worst['change']:+,.2f}。")
            if attribution.get("group_compare"):
                top = attribution["group_compare"][0]
                findings.append(f"按 {selected['dimension_column']} 分组，{top['维度']} 的 {metric_label} 最高（{top['指标值']}）。")
        elif intent == "group_compare" and attribution.get("group_compare"):
            groups = attribution["group_compare"]
            top, bottom = groups[0], groups[-1]
            summary += f" 按 {selected['dimension_column']} 分组，{metric_label} 最高的是 {top['维度']}（{top['指标值']}）。"
            findings.append(f"共 {len(groups)} 个分组（最多展示 10 个），最低的是 {bottom['维度']}（{bottom['指标值']}）。")
            total = sum(float(g["指标值"] or 0) for g in groups)
            if total:
                findings.append(f"{top['维度']} 占展示分组合计的 {float(top['指标值']) / total:.2%}。")
        elif intent == "missing":
            missing = attribution.get("missing") or []
            if missing:
                top = missing[0]
                summary += f" 缺失最严重的字段是 {top['字段']}，缺失率 {float(top['缺失率']):.2%}。"
                findings.append(f"共有 {len(missing)} 个字段存在缺失；缺失按 NULL 和空字符串统一统计。")
            else:
                summary += " 所有业务字段都没有缺失值。"
        elif intent == "anomaly":
            anomalies = attribution.get("anomaly") or []
            if anomalies:
                top = anomalies[0]
                label = f"（{top['记录']}）" if top.get("记录") is not None else ""
                summary += f" 发现 {len(anomalies)} 个 {selected['metric_column']} 异常值，最极端的是 {top['指标值']}{label}，z={top['z_score']}。"
            else:
                summary += f" {selected['metric_column']} 没有偏离均值 {ANOMALY_Z_THRESHOLD:g} 个标准差以上的异常值。"
            findings.append(f"异常检测基于 {selected['metric_column']} 的 z-score（|z|≥{ANOMALY_Z_THRESHOLD:g}）做初筛。")
        elif intent == "distribution":
            summary += f" 已给出 {selected['metric_column']} 的均值、中位数和极值。"
            findings.append("如需解释差异来源，可追问“按某个分类字段对比”。")
        else:
            summary += " 当前问题未匹配到明确的分析意图，已返回字段画像与数据质量检查。"
            findings.append("如果需要回答严格的“为什么”，建议提供时间列、指标列和可解释的分类维度列。")

        recommendations = _recommendations(intent, selected)
        state["report"] = {
            "summary": summary,
            "findings": findings,
            "recommendations": recommendations,
            "markdown": render_markdown("CSV 通用分析报告", summary, findings, recommendations),
        }
        return "生成导入表分析报告，包含证据和限制说明。"

    return run_node(state, "report", work)


# ---------------------------------------------------------------------------
# 字段画像
# ---------------------------------------------------------------------------


def _load_columns(table_name: str) -> list[dict[str, Any]]:
    result = execute_sql(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :table_name
        ORDER BY ordinal_position
        """,
        {"table_name": table_name},
    )
    return [
        {"name": row[0], "data_type": row[1], "nullable": row[2] == "YES"}
        for row in result["data"]
        if row[0] not in TECHNICAL_COLUMNS
    ]


def _role_for(col: dict[str, Any], row_count: int) -> str:
    name = col["name"].lower()
    raw = col["name"]
    typ = col["data_type"].lower()
    distinct = col.get("distinct_count", 0)
    if name in {"id"} or name.endswith("_id") or any(token in raw for token in ("编号", "编码", "学号", "工号", "单号", "序号")):
        return "id"
    if any(t in typ for t in DATE_TYPES):
        return "date"
    # 文本型的"月份/日期"列不能 ::timestamp 强转（可能是 "5月"、"2026Q1"），按原值作为周期分组
    if any(token in raw for token in _PERIOD_TOKENS) or any(token in name for token in ("date", "time", "month", "period")):
        return "period"
    if any(t in typ for t in NUMERIC_TYPES):
        return "numeric"
    if any(t in typ for t in TEXT_TYPES):
        # 几乎每行都不同的文本列（姓名、备注等）不适合当分组维度
        if row_count >= 10 and distinct > max(MAX_GROUPS, row_count * 0.8):
            return "text"
        return "dimension"
    return "other"


def _semantic_for(col: dict[str, Any]) -> str:
    name = col["name"].lower()
    raw = col["name"]
    if col["role"] in {"date", "period"}:
        return "date"
    if any(token in raw for token in ("金额", "销售额", "收入", "价格", "费用", "成本", "利润")) or any(token in name for token in ("amount", "sales", "revenue", "price", "cost", "profit")):
        return "money"
    if any(token in raw for token in ("数量", "人数", "次数", "库存")) or any(token in name for token in ("qty", "quantity", "count", "stock")):
        return "quantity"
    if any(token in raw for token in ("地区", "省", "市", "城市", "区域")) or any(token in name for token in ("region", "province", "city")):
        return "geo"
    if any(token in raw for token in ("渠道", "来源")) or "channel" in name:
        return "channel"
    if any(token in raw for token in ("类别", "品类", "类型", "状态", "性别", "院系", "部门", "专业", "班级", "结果")) or any(token in name for token in ("category", "type", "status")):
        return "category"
    return col["role"]


def _column(profile: dict[str, Any], name: str) -> dict[str, Any]:
    return next(col for col in profile["columns"] if col["name"] == name)


def _mentions_column(profile: dict[str, Any], question: str, roles: tuple[str, ...]) -> bool:
    return any(col["name"] in question for col in profile["columns"] if col["role"] in roles)


def _pick_column(profile: dict[str, Any], roles: tuple[str, ...], question: str) -> str | None:
    candidates = [col for col in profile["columns"] if col["role"] in roles]
    if not candidates:
        return None
    # 问题里点名的字段优先，最长匹配优先（避免"销售额"被"销售"抢先）
    mentioned = sorted((col for col in candidates if col["name"] in question), key=lambda c: len(c["name"]), reverse=True)
    if mentioned:
        return mentioned[0]["name"]
    if "numeric" in roles:
        for semantic in ("money", "quantity"):
            for col in candidates:
                if col["semantic_type"] == semantic:
                    return col["name"]
    # 优先真实日期类型，其次文本周期列
    candidates.sort(key=lambda c: (c["role"] != "date", c.get("null_count", 0)))
    return candidates[0]["name"]


def _pick_dimension(profile: dict[str, Any], question: str) -> str | None:
    candidates = [col for col in profile["columns"] if col["role"] == "dimension"]
    mentioned = sorted((col for col in candidates if col["name"] in question), key=lambda c: len(c["name"]), reverse=True)
    if mentioned:
        return mentioned[0]["name"]
    usable = [col for col in candidates if 1 < col.get("distinct_count", 0) <= MAX_GROUPS]
    for semantic in ("category", "channel", "geo"):
        for col in usable:
            if col["semantic_type"] == semantic:
                return col["name"]
    if usable:
        # 分组数适中、缺失少的维度更有解释力
        usable.sort(key=lambda c: (c.get("null_count", 0), c.get("distinct_count", 0)))
        return usable[0]["name"]
    return None


def _label_column(profile: dict[str, Any]) -> str | None:
    """异常值需要能定位到具体记录：优先用 id 列，其次用高基数文本列（如姓名）。"""
    for role in ("id", "text", "dimension"):
        for col in profile["columns"]:
            if col["role"] == role:
                return col["name"]
    return None


# ---------------------------------------------------------------------------
# SQL 模板
# ---------------------------------------------------------------------------


def _agg_expr(metric_col: str | None) -> str:
    if metric_col is None:
        return "COUNT(*)"
    return f"ROUND(SUM({quote_identifier(metric_col)})::numeric, 2)"


def _overview_query(table_q: str) -> dict[str, str]:
    return {
        "id": "overview",
        "purpose": "表级数据概览",
        "sql": f'SELECT COUNT(*) AS "行数" FROM {table_q}',
    }


def _missing_query(table_q: str, columns: list[dict[str, Any]]) -> dict[str, str]:
    parts = []
    for col in columns[:30]:
        col_q = quote_identifier(col["name"])
        parts.append(
            f"""SELECT '{col['name'].replace("'", "''")}' AS "字段",
                       COUNT(*) FILTER (WHERE {NULL_SQL.format(col=col_q)}) AS "缺失数",
                       ROUND((COUNT(*) FILTER (WHERE {NULL_SQL.format(col=col_q)}))::numeric / NULLIF(COUNT(*), 0), 4) AS "缺失率"
                FROM {table_q}"""
        )
    return {"id": "missing", "purpose": "字段缺失率检查", "sql": " UNION ALL ".join(parts) + ' ORDER BY "缺失率" DESC, "缺失数" DESC'}


def _trend_query(table_q: str, date_col: str, date_is_typed: bool, metric_col: str | None) -> dict[str, str]:
    date_q = quote_identifier(date_col)
    period_expr = f"to_char(date_trunc('month', {date_q}), 'YYYY-MM')" if date_is_typed else f"TRIM({date_q}::text)"
    metric_filter = f" AND {quote_identifier(metric_col)} IS NOT NULL" if metric_col else ""
    return {
        "id": "trend",
        "purpose": f"按 {date_col} 观察{'记录数' if metric_col is None else ' ' + metric_col}变化",
        "sql": f"""
            SELECT {period_expr} AS "周期",
                   {_agg_expr(metric_col)} AS "指标值",
                   COUNT(*) AS "记录数"
            FROM {table_q}
            WHERE NULLIF(TRIM({date_q}::text), '') IS NOT NULL{metric_filter}
            GROUP BY 1
            ORDER BY 1
            LIMIT 120
        """,
    }


def _group_query(table_q: str, dim_col: str, metric_col: str | None) -> dict[str, str]:
    dim_q = quote_identifier(dim_col)
    where = f"WHERE {quote_identifier(metric_col)} IS NOT NULL" if metric_col else ""
    return {
        "id": "group_compare",
        "purpose": f"按 {dim_col} 对比{'记录数' if metric_col is None else ' ' + metric_col}",
        "sql": f"""
            SELECT COALESCE(NULLIF(TRIM({dim_q}::text), ''), '(空)') AS "维度",
                   {_agg_expr(metric_col)} AS "指标值",
                   COUNT(*) AS "记录数"
            FROM {table_q}
            {where}
            GROUP BY 1
            ORDER BY "指标值" DESC, "维度"
            LIMIT 20
        """,
    }


def _anomaly_query(table_q: str, metric_col: str, label_col: str | None) -> dict[str, str]:
    metric_q = quote_identifier(metric_col)
    label_select = f'{quote_identifier(label_col)}::text AS "记录", ' if label_col else ""
    return {
        "id": "anomaly",
        "purpose": f"{metric_col} 异常值候选（|z|≥{ANOMALY_Z_THRESHOLD:g}）",
        "sql": f"""
            WITH stats AS (
                SELECT AVG({metric_q}) AS avg_v, STDDEV_POP({metric_q}) AS std_v
                FROM {table_q}
                WHERE {metric_q} IS NOT NULL
            ),
            scored AS (
                SELECT {label_select}{metric_q} AS "指标值",
                       ROUND((({metric_q} - stats.avg_v) / NULLIF(stats.std_v, 0))::numeric, 4) AS "z_score"
                FROM {table_q}, stats
                WHERE {metric_q} IS NOT NULL
            )
            SELECT * FROM scored
            WHERE ABS("z_score") >= {ANOMALY_Z_THRESHOLD}
            ORDER BY ABS("z_score") DESC
            LIMIT 20
        """,
    }


def _distribution_query(table_q: str, metric_col: str) -> dict[str, str]:
    metric_q = quote_identifier(metric_col)
    return {
        "id": "distribution",
        "purpose": f"{metric_col} 分布摘要",
        "sql": f"""
            SELECT ROUND(AVG({metric_q})::numeric, 2) AS "平均值",
                   ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {metric_q}))::numeric, 2) AS "中位数",
                   ROUND(MIN({metric_q})::numeric, 2) AS "最小值",
                   ROUND(MAX({metric_q})::numeric, 2) AS "最大值",
                   COUNT({metric_q}) AS "有效记录数"
            FROM {table_q}
        """,
    }


# ---------------------------------------------------------------------------
# 结果整理
# ---------------------------------------------------------------------------


def _snapshot_metric(name: str, value: Any) -> dict[str, Any]:
    """无对比基期的快照指标：change_rate=None，前端不展示"环比"。"""
    return {"name": name, "previous": None, "current": value, "change": None, "change_rate": None}


def _metric_label(selected: dict[str, Any]) -> str:
    if selected.get("aggregate") == "count" or not selected.get("metric_column"):
        return "记录数"
    return selected["metric_column"]


def _trend_changes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for prev, cur in zip(rows, rows[1:]):
        previous = float(prev.get("指标值") or 0)
        current = float(cur.get("指标值") or 0)
        change = current - previous
        result.append({
            "周期": cur.get("周期"),
            "previous": previous,
            "current": current,
            "change": change,
            "change_rate": safe_rate(change, previous),
        })
    return result


def _intent_label(intent: str) -> str:
    return {
        "missing": "检查字段缺失和数据完整性",
        "anomaly": "检测数值异常和缺失问题",
        "trend": "按时间观察指标变化并解释方向",
        "group_compare": "按维度做分组贡献和排名",
        "distribution": "计算数值指标分布摘要",
        "profile": "生成表结构画像和可分析方向",
    }.get(intent, "通用分析")


def _recommendations(intent: str, selected: dict[str, Any]) -> list[str]:
    if intent == "trend":
        return [
            "如果要进一步回答严格归因，建议补充可解释维度列，如地区、渠道、类别或状态。",
            "对下降周期继续按主要维度下钻，比较各分组对总变化的贡献。",
        ]
    if intent == "missing":
        return [
            "优先确认高缺失字段是否为业务上允许为空，避免直接参与指标计算。",
            "导入前可补充字段说明，帮助 Agent 区分未知、无和未填写。",
        ]
    if intent == "anomaly":
        return [
            "异常值只是统计候选，需要结合原始记录核对是否为录入错误、极端业务事件或真实波动。",
            "建议增加日期或分类维度，定位异常集中在哪个时间段或分组。",
        ]
    if intent == "group_compare":
        return [
            f"优先检查 {selected.get('dimension_column') or '主要维度'} 头部分组与尾部分组的差异来源。",
            "如果要回答为什么变化，建议加入时间列后做分组环比贡献分析。",
        ]
    return [
        "当前字段不足以做强归因，可先补充时间列、数值指标列和业务维度列。",
        "建议为 CSV 使用清晰列名，例如日期、销售额、地区、渠道、类别。",
    ]


def _profile_summary(profile: dict[str, Any]) -> str:
    if not profile:
        return ""
    selected = profile.get("selected", {})
    return (
        f"表 {profile.get('table_name')} 共 {profile.get('row_count')} 行，"
        f"识别意图 {selected.get('intent')}，"
        f"时间列 {selected.get('date_column') or '无'}，"
        f"指标列 {selected.get('metric_column') or '无'}，"
        f"维度列 {selected.get('dimension_column') or '无'}。"
    )
