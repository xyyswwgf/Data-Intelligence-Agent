"""
PostgreSQL data access layer.

The public API intentionally matches the original database module so existing
routes and CSV import code can evolve without changing every caller at once.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from backend.config import (
    DATABASE_URL,
    QUERY_MAX_ROWS,
    QUERY_TIMEOUT_MS,
    SQL_READER_ROLE,
    SYSTEM_TABLE_PREFIXES,
    SYSTEM_TABLES,
)


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

# PostgreSQL 标识符上限是 63 字节（NAMEDATALEN - 1），超出部分会被静默截断
PG_MAX_IDENTIFIER_BYTES = 63


@contextmanager
def get_connection():
    with engine.begin() as conn:
        yield conn


def quote_identifier(identifier: str) -> str:
    if not identifier or "\x00" in identifier:
        raise ValueError("Invalid SQL identifier")
    return '"' + identifier.replace('"', '""') + '"'


def truncate_identifier(identifier: str, max_bytes: int = PG_MAX_IDENTIFIER_BYTES) -> str:
    """按 UTF-8 字节截断标识符，避免中文表名/列名被 PostgreSQL 静默截断后对不上。"""
    encoded = identifier.encode("utf-8")
    if len(encoded) <= max_bytes:
        return identifier
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def is_system_table(table_name: str) -> bool:
    return table_name in SYSTEM_TABLES or table_name.startswith(SYSTEM_TABLE_PREFIXES)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def _run_select(conn: Connection, sql: str, params: dict | None = None) -> list[dict]:
    result = conn.execute(text(sql), params or {})
    return [dict(row._mapping) for row in result]


def get_table_names(include_system: bool = False) -> list[str]:
    sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    with get_connection() as conn:
        tables = [row["table_name"] for row in _run_select(conn, sql)]
    if include_system:
        return tables
    return [t for t in tables if not is_system_table(t)]


def get_table_row_counts() -> dict[str, int]:
    """一次查询拿到所有业务表的行数，避免逐表 COUNT(*) 的 N+1 查询。"""
    tables = get_table_names()
    if not tables:
        return {}
    union_sql = " UNION ALL ".join(
        f"SELECT :t{i} AS table_name, COUNT(*) AS cnt FROM {quote_identifier(t)}"
        for i, t in enumerate(tables)
    )
    params = {f"t{i}": t for i, t in enumerate(tables)}
    with get_connection() as conn:
        return {row["table_name"]: int(row["cnt"]) for row in _run_select(conn, union_sql, params)}


def get_ddl() -> str:
    """Return PostgreSQL-style CREATE TABLE metadata for LLM context."""
    sql = """
        SELECT
            c.table_name,
            c.column_name,
            c.data_type,
            c.character_maximum_length,
            c.numeric_precision,
            c.numeric_scale,
            c.is_nullable,
            c.column_default,
            obj_description(format('%I.%I', c.table_schema, c.table_name)::regclass) AS table_comment,
            col_description(format('%I.%I', c.table_schema, c.table_name)::regclass::oid, c.ordinal_position) AS column_comment
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position
    """
    with get_connection() as conn:
        rows = _run_select(conn, sql)

    grouped: dict[str, list[dict]] = {}
    comments: dict[str, str | None] = {}
    for row in rows:
        table_name = row["table_name"]
        if is_system_table(table_name):
            continue
        grouped.setdefault(table_name, []).append(row)
        comments[table_name] = row.get("table_comment")

    ddl_parts: list[str] = []
    for table_name, columns in grouped.items():
        ddl_parts.append(f"CREATE TABLE {quote_identifier(table_name)} (")
        col_lines = []
        for col in columns:
            data_type = _format_pg_type(col)
            nullable = "NOT NULL" if col["is_nullable"] == "NO" else "NULL"
            default = f" DEFAULT {col['column_default']}" if col.get("column_default") else ""
            comment = f" -- {col['column_comment']}" if col.get("column_comment") else ""
            col_lines.append(
                f"  {quote_identifier(col['column_name'])} {data_type} {nullable}{default}{comment}"
            )
        ddl_parts.append(",\n".join(col_lines))
        table_comment = f" -- {comments.get(table_name)}" if comments.get(table_name) else ""
        ddl_parts.append(f");{table_comment}\n")
    return "\n".join(ddl_parts).strip()


def _format_pg_type(col: dict) -> str:
    data_type = col["data_type"]
    if data_type == "character varying" and col.get("character_maximum_length"):
        return f'VARCHAR({col["character_maximum_length"]})'
    if data_type == "numeric" and col.get("numeric_precision"):
        scale = col.get("numeric_scale") or 0
        return f'NUMERIC({col["numeric_precision"]},{scale})'
    if data_type == "timestamp with time zone":
        return "TIMESTAMPTZ"
    if data_type == "timestamp without time zone":
        return "TIMESTAMP"
    return data_type.upper()


def _format_result(columns: list[str], rows) -> dict:
    data = [[_json_value(value) for value in row] for row in rows]
    return {"columns": columns, "data": data, "row_count": len(data)}


def execute_sql(sql: str, params: dict | None = None) -> dict:
    """执行服务端构造的 SQL（可带绑定参数）。即使结果为空也保留列名。"""
    with get_connection() as conn:
        result = conn.execute(text(sql), params or {})
        columns = list(result.keys())
        return _format_result(columns, result.fetchall())


def execute_readonly_sql(
    sql: str,
    max_rows: int = QUERY_MAX_ROWS,
    timeout_ms: int = QUERY_TIMEOUT_MS,
) -> dict:
    """
    执行不可信 SQL（LLM 生成 / Agent 生成）的唯一入口 —— 纵深防御第三层。

    - READ ONLY 事务：即使 sql_guard 被绕过，数据库也会拒绝任何写操作
    - SET LOCAL ROLE：切换到只读角色，只能读业务表，读不到 users / audit_logs / wiki_* 等系统表
    - statement_timeout：防止笛卡尔积、pg_sleep 类慢查询拖垮数据库
    - 行数上限：防止一次返回百万行把后端和浏览器撑爆
    - 直接走 DBAPI 游标且不传参数，SQL 中的 '%'、':name' 等字面量不会被当成占位符
    """
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            if SQL_READER_ROLE:
                conn.execute(text(f"SET LOCAL ROLE {quote_identifier(SQL_READER_ROLE)}"))
            cursor = conn.connection.driver_connection.cursor()
            try:
                cursor.execute(sql)
                if cursor.description is None:
                    return {"columns": [], "data": [], "row_count": 0, "truncated": False}
                columns = [desc.name for desc in cursor.description]
                rows = cursor.fetchmany(max_rows + 1)
            finally:
                cursor.close()
    truncated = len(rows) > max_rows
    result = _format_result(columns, rows[:max_rows])
    result["truncated"] = truncated
    return result


def execute_ddl(sql: str) -> None:
    with get_connection() as conn:
        conn.execute(text(sql))


def execute_insert(sql: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    with get_connection() as conn:
        result = conn.execute(text(sql), rows)
        return result.rowcount or len(rows)


def table_exists(table_name: str) -> bool:
    sql = """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = :table_name
    """
    with get_connection() as conn:
        return conn.execute(text(sql), {"table_name": table_name}).first() is not None


def drop_table(table_name: str, conn: Connection | None = None) -> None:
    if not table_name.startswith("csv_"):
        raise ValueError(
            f"安全限制：不允许删除非导入表 '{table_name}'。只有 csv_ 前缀的导入表可以被删除。"
        )
    sql = f"DROP TABLE IF EXISTS {quote_identifier(table_name)}"
    if conn is not None:
        conn.execute(text(sql))
    else:
        execute_ddl(sql)
