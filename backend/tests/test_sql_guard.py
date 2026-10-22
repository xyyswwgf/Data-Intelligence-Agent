import pytest

from backend.sql_guard import strip_trailing_semicolon, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM fact_orders",
        "select 1;",
        "WITH t AS (SELECT 1 AS a) SELECT a FROM t",
        "EXPLAIN SELECT 1",
        "(SELECT 1) UNION (SELECT 2)",
        "SELECT * FROM t WHERE status = 'UPDATE; DROP'",  # 关键字和分号在字符串里
        'SELECT "update", "delete_flag" FROM t',          # 关键字是带引号的列名
        "SELECT * FROM t WHERE name LIKE '%张%' -- 注释; drop",
        "SELECT updated_at, created_by FROM t",           # 关键字只是标识符前缀
    ],
)
def test_allows_read_only_queries(sql):
    assert validate_sql(sql) == (True, "OK")


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "DROP TABLE fact_orders",
        "DELETE FROM fact_orders",
        "SELECT 1; DROP TABLE fact_orders",
        "SELECT 1; SELECT 2",
        "SELECT * INTO backup FROM fact_orders",          # PostgreSQL 中会建表
        "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT '--', 1; DELETE FROM t",                  # 字符串里伪造注释
        "SELECT 'unterminated",
        "COPY t TO '/tmp/x'",
        "UPDATE t SET a = 1",
    ],
)
def test_blocks_dangerous_queries(sql):
    is_safe, reason = validate_sql(sql)
    assert not is_safe
    assert reason


def test_strip_trailing_semicolon():
    assert strip_trailing_semicolon("SELECT 1 ; ") == "SELECT 1"
