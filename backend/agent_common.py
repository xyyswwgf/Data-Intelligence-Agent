"""
Agent 公共组件 — 经营分析 Agent 与 CSV 通用 Agent 共享的节点执行、SQL 执行、
图表构造、报告渲染和分析历史存储。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from backend.config import ANALYSIS_HISTORY_LIMIT
from backend.database import execute_readonly_sql
from backend.sql_guard import validate_sql


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
# 分析历史（进程内、有界、线程安全）
# ---------------------------------------------------------------------------
# FastAPI 同步路由运行在线程池里，多个请求可能并发写入，因此加锁；
# 超过上限时淘汰最旧记录，避免长时间运行后内存无限增长。

_HISTORY: OrderedDict[str, dict[str, Any]] = OrderedDict()
_HISTORY_LOCK = threading.Lock()


def save_analysis_result(response: dict[str, Any]) -> None:
    with _HISTORY_LOCK:
        _HISTORY[response["run_id"]] = response
        _HISTORY.move_to_end(response["run_id"])
        while len(_HISTORY) > ANALYSIS_HISTORY_LIMIT:
            _HISTORY.popitem(last=False)


def list_history(limit: int = 20) -> list[dict[str, Any]]:
    with _HISTORY_LOCK:
        items = list(_HISTORY.values())[-limit:]
    return [
        {
            "run_id": item["run_id"],
            "question": item["question"],
            "table_name": item.get("table_name"),
            "status": item["status"],
            "summary": item.get("report", {}).get("summary", ""),
        }
        for item in reversed(items)
    ]


def get_history_detail(run_id: str) -> dict[str, Any] | None:
    with _HISTORY_LOCK:
        return _HISTORY.get(run_id)
