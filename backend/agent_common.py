"""
Agent 公共组件 — 经营分析 Agent 与 CSV 通用 Agent 共享的节点执行、SQL 执行、
图表构造、报告渲染和分析历史存储。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable

from sqlalchemy import text

from backend.config import ANALYSIS_HISTORY_LIMIT
from backend.database import execute_readonly_sql, get_connection
from backend.sql_guard import validate_sql

logger = logging.getLogger("nl2sql.agent")


# ---------------------------------------------------------------------------
# 节点执行
# ---------------------------------------------------------------------------


def run_node(state: dict, name: str, work: Callable[[], str]) -> dict:
    """
    执行单个 Agent 节点并记录步骤耗时。

    上游节点失败后，下游节点直接标记为 skipped，而不是继续执行并抛出一串
    KeyError —— 这样前端看到的是"哪一步真正失败"，而不是级联噪音。
    """
    steps = state.setdefault("steps", [])
    errors = state.setdefault("errors", [])
    if errors:
        steps.append({"name": name, "status": "skipped", "summary": "上游步骤失败，已跳过。", "elapsed_ms": 0})
        return state

    started = time.perf_counter()
    try:
        summary = work()
        status = "completed"
    except Exception as exc:
        status = "failed"
        summary = str(exc)
        errors.append({"node": name, "message": str(exc)})
    steps.append({
        "name": name,
        "status": status,
        "summary": summary,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    })
    return state


def execute_queries(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """所有 Agent SQL 统一经过 sql_guard + 只读事务执行。"""
    executed = []
    for query in queries:
        sql = " ".join(query["sql"].split())
        is_safe, reason = validate_sql(sql)
        if not is_safe:
            raise ValueError(f"{query['id']} 未通过 SQL 安全校验: {reason}")
        executed.append({**query, "sql": sql, **execute_readonly_sql(sql)})
    return executed


def rows_of(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(zip(query["columns"], row)) for row in query.get("data", [])]


def safe_rate(change: float, base: float) -> float | None:
    """环比/变化率：基期为 0 时返回 None（前端显示 "—"），而不是误导性的 0%。"""
    return change / base if base else None


# ---------------------------------------------------------------------------
# 图表
# ---------------------------------------------------------------------------

_GRID = {"left": "4%", "right": "4%", "top": "10%", "bottom": "14%", "containLabel": True}


def line_chart(categories: list[str], series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0},
        "grid": _GRID,
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": [{**item, "type": "line", "smooth": True} for item in series],
    }


def bar_chart(categories: list[str], series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0},
        "grid": _GRID,
        "xAxis": {"type": "category", "data": categories, "axisLabel": {"interval": 0, "rotate": 30 if len(categories) > 6 else 0}},
        "yAxis": {"type": "value"},
        "series": [{**item, "type": "bar"} for item in series],
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def render_markdown(title: str, summary: str, findings: list[str], recommendations: list[str]) -> str:
    lines = [f"# {title}", "", "## 摘要", summary, "", "## 关键发现"]
    lines.extend(f"- {item}" for item in findings)
    lines.extend(["", "## 建议动作"])
    lines.extend(f"- {item}" for item in recommendations)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 分析历史（持久化到 agent_runs，服务重启不丢失）
# ---------------------------------------------------------------------------


def save_analysis_result(response: dict[str, Any], user_id: int | None = None) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                text("""
                    INSERT INTO agent_runs (run_id, question, table_name, status, summary, result, created_by)
                    VALUES (:rid, :q, :t, :s, :summary, CAST(:result AS jsonb), :uid)
                    ON CONFLICT (run_id) DO NOTHING
                """),
                {
                    "rid": response["run_id"],
                    "q": response["question"],
                    "t": response.get("table_name"),
                    "s": response["status"],
                    "summary": response.get("report", {}).get("summary", ""),
                    "result": json.dumps(response, ensure_ascii=False, default=str),
                    "uid": user_id,
                },
            )
    except Exception as exc:  # 历史保存失败不影响本次分析结果返回
        logger.error("保存分析历史失败: %s", exc)


def list_history(limit: int = 20, user_id: int | None = None) -> list[dict[str, Any]]:
    """user_id 为 None 时返回全部（管理员视角），否则只返回该用户自己的分析。"""
    with get_connection() as conn:
        rows = conn.execute(
            text("""
                SELECT run_id::text AS run_id, question, table_name, status, summary, created_at
                FROM agent_runs
                WHERE (CAST(:uid AS BIGINT) IS NULL OR created_by = CAST(:uid AS BIGINT))
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"uid": user_id, "limit": min(limit, ANALYSIS_HISTORY_LIMIT)},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_history_detail(run_id: str, user_id: int | None = None) -> dict[str, Any] | None:
    try:
        uuid.UUID(str(run_id))
    except ValueError:
        return None
    with get_connection() as conn:
        row = conn.execute(
            text("SELECT result FROM agent_runs WHERE run_id = CAST(:rid AS uuid) AND (CAST(:uid AS BIGINT) IS NULL OR created_by = CAST(:uid AS BIGINT))"),
            {"rid": run_id, "uid": user_id},
        ).first()
    return row[0] if row else None
