"""
自然语言查询（NL2SQL）核心流程，供 /api/query、对话会话和推理 Agent 复用

  LLM 生成 SQL → sql_guard 安全校验 → 只读角色 + READ ONLY 事务执行
  → 执行报错时把错误回传给 LLM 自我修正（最多 LLM_SQL_REPAIR_ATTEMPTS 次）
"""

from __future__ import annotations

import logging

from backend.config import LLM_SQL_REPAIR_ATTEMPTS
from backend.database import execute_readonly_sql
from backend.llm_service import generate_sql
from backend.sql_guard import strip_trailing_semicolon, validate_sql

logger = logging.getLogger("nl2sql")


class QueryUnanswerable(ValueError):
    """问题无法转换为 SQL。"""


class QueryLLMError(RuntimeError):
    """大模型调用失败或未配置。"""


class QueryBlocked(PermissionError):
    """SQL 未通过安全校验。"""


class QueryFailed(RuntimeError):
    """多次修正后仍执行失败。"""


def answer_query(question: str, provider=None) -> dict:
    logger.info("收到查询: %s", question)
    failed_sql: str | None = None
    last_error: str | None = None
    for attempt in range(1, LLM_SQL_REPAIR_ATTEMPTS + 2):
        try:
            sql = generate_sql(question, failed_sql=failed_sql, error=last_error, provider=provider)
            logger.info("LLM 生成 SQL (第 %d 次): %s", attempt, sql[:200])
        except ValueError as exc:
            raise QueryUnanswerable(str(exc)) from exc
        except RuntimeError as exc:
            raise QueryLLMError(str(exc)) from exc

        # 安全拦截不重试
        is_safe, reason = validate_sql(sql)
        if not is_safe:
            logger.warning("SQL 安全拦截: %s — %s", reason, sql[:200])
            raise QueryBlocked(f"SQL 安全校验未通过: {reason}。系统只允许 SELECT 查询。")

        try:
            result = execute_readonly_sql(strip_trailing_semicolon(sql))
        except Exception as exc:
            failed_sql = sql
            last_error = str(getattr(exc, "orig", None) or exc).strip()
            logger.warning("SQL 执行失败 (第 %d 次): %s — %s", attempt, last_error[:200], sql[:200])
            continue

        logger.info("查询成功，返回 %d 行数据%s", result["row_count"], "（已截断）" if result["truncated"] else "")
        return {
            "sql": sql,
            "columns": result["columns"],
            "data": result["data"],
            "row_count": result["row_count"],
            "truncated": result["truncated"],
            "attempts": attempt,
        }

    raise QueryFailed(f"SQL 执行失败（已自动修正 {LLM_SQL_REPAIR_ATTEMPTS} 次）: {last_error}")
