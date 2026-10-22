import pytest

from backend.csv_importer import (
    build_create_table_sql,
    derive_table_name,
    infer_column_types,
    normalize_column_type,
    normalize_table_name,
    read_csv,
    sanitize_column_name,
)


def _types(rows, headers=("c",)):
    return [c["type"] for c in infer_column_types(list(headers), rows)]


def test_leading_zero_ids_stay_text():
    assert _types([["007"], ["012"]])[0].startswith("VARCHAR")


def test_huge_digit_strings_stay_text():
    assert _types([["110101199003071234"], ["1"]]) == ["BIGINT"]
    assert _types([["99999999999999999999999"]])[0].startswith("VARCHAR")


def test_numeric_inference():
    assert _types([["1"], ["2"]]) == ["INT"]
    assert _types([["1.5"], ["20.25"]]) == ["DECIMAL(4,2)"]
    assert _types([["1e5"], ["2"]])[0].startswith("VARCHAR")  # 科学计数法不当成 DECIMAL
    assert _types([["2026-05-01"], ["2026/5/2"]]) == ["DATE"]
    assert _types([["2026-05-01 10:00:00"]]) == ["TIMESTAMP"]
    assert _types([["x" * 1500]]) == ["TEXT"]


def test_type_override_whitelist_blocks_ddl_injection():
    assert normalize_column_type("decimal(10, 2)") == "DECIMAL(10, 2)"
    with pytest.raises(ValueError):
        normalize_column_type("INT); DROP TABLE fact_orders; --")
    with pytest.raises(ValueError):
        infer_column_types(["a"], [["1"]], {"a": "INT DEFAULT pg_sleep(10)"})


def test_identifiers_fit_postgres_63_bytes():
    long_cn = "很长的中文列名" * 10
    used = set()
    a = sanitize_column_name(long_cn + "甲", used)
    b = sanitize_column_name(long_cn + "乙", used)
    assert len(a.encode()) <= 63 and len(b.encode()) <= 63
    assert a != b
    table = derive_table_name(long_cn + ".csv")
    assert table.startswith("csv_") and len(table.encode()) <= 63


def test_reserved_and_quote_characters_in_column_names():
    used = set()
    assert sanitize_column_name("_id", used) == "src_id"
    assert '"' not in sanitize_column_name('a"b', used)


def test_normalize_table_name():
    assert normalize_table_name("my table; drop") == "csv_my_table_drop"
    assert normalize_table_name("csv_销售") == "csv_销售"
    assert normalize_table_name("   ") == "csv_imported"


def test_read_csv_auto_header_and_gbk():
    content = "学生成绩汇总\n序号,姓名,成绩\n1,张三,90\n2,李四,85\n".encode("gbk")
    parsed = read_csv(content, header_row=0)
    assert parsed.encoding == "gb18030"
    assert parsed.header_row == 2
    assert parsed.data_start_row == 3
    assert parsed.headers == ["序号", "姓名", "成绩"]
    assert parsed.rows == [["1", "张三", "90"], ["2", "李四", "85"]]


def test_read_csv_keeps_extra_data_columns():
    parsed = read_csv(b"a,b\n1,2,3\n")
    assert len(parsed.headers) == 3


def test_create_table_sql_always_has_imported_at():
    cols = [{"name": "学号", "type": "VARCHAR(100)", "nullable": False}]
    ddl = build_create_table_sql("csv_t", cols, primary_key_column="学号")
    assert '"_imported_at"' in ddl and "PRIMARY KEY" in ddl and '"_id"' not in ddl
