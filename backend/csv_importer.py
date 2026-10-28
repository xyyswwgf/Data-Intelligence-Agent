"""
================================================================================
CSV 导入引擎 — 解析、类型推断、建表、批量写入
================================================================================

核心功能:
  - 支持多行合并表头（Excel 合并单元格场景）
  - 支持跳过末尾非数据行（汇总、签字行等）
  - 自动编码检测（UTF-8 / GBK / GB2312）
  - 自动分隔符检测（逗号 / 制表符）
  - 智能类型推断 + 用户手动覆盖
  - 自定义主键列

安全设计:
  - 所有 SQL 由服务端构造，不经过 sql_guard
  - 列名和表名经过清理 + 双引号转义，并按 PostgreSQL 63 字节上限截断
  - 列类型（含用户覆盖）必须命中白名单，杜绝 DDL 注入
  - 数据值通过 SQLAlchemy executemany 参数化传递
  - 覆盖 + 建表 + 写入在同一事务内完成，失败自动回滚，不会留下半张表或误删旧表
================================================================================
"""

import csv
import io
import re
import datetime
import logging
from dataclasses import dataclass

from sqlalchemy import text

from backend.database import drop_table, get_connection, quote_identifier, truncate_identifier

logger = logging.getLogger("nl2sql.csv_importer")

# ---------------------------------------------------------------------------
_NULL_MARKERS = {"", "null", "none", "na", "n/a", "-", "nil", "无", "空", "/"}
_MIN_VARCHAR_LEN = 100
_MAX_VARCHAR_LEN = 2000
_BIGINT_MAX = 9_223_372_036_854_775_807
_INT_MAX = 2_147_483_647
# 导入时自动生成的技术列，用户 CSV 中的同名列会被重命名，避免建表冲突
RESERVED_COLUMN_NAMES = {"_id", "_imported_at"}

# 允许出现在 CREATE TABLE 中的列类型（用户覆盖的类型也必须匹配其一）
_ALLOWED_TYPE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^(SMALLINT|INT|INTEGER|BIGINT|REAL|DOUBLE PRECISION|NUMERIC|DECIMAL|DATE|TIMESTAMP|TIMESTAMPTZ|TEXT|BOOLEAN)$",
        r"^(NUMERIC|DECIMAL)\(\s*\d{1,2}\s*(,\s*\d{1,2}\s*)?\)$",
        r"^(VARCHAR|CHAR)\(\s*\d{1,5}\s*\)$",
    )
]

# 非数据行的特征（用于自动检测尾部行）
_FOOTER_KEYWORDS = ["合计", "总计", "签字", "审核", "日期", "院长", "公章", "备注", "说明"]


# ═══════════════════════════════════════════════════════════════
# 1. CSV 解析
# ═══════════════════════════════════════════════════════════════

@dataclass
class ParsedCsv:
    headers: list[str]
    rows: list[list[str]]
    encoding: str
    delimiter: str
    header_row: int          # 实际使用的表头行（1-based，自动检测后的结果）
    data_start_row: int      # 实际数据起始行（1-based）
    total_lines: int         # 有效（非空）逻辑行总数


def detect_encoding(file_bytes: bytes) -> tuple[str, str]:
    """返回 (解码后的文本, 编码名)。GB18030 是 GBK/GB2312 的超集，覆盖国内常见 Excel 导出。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return file_bytes.decode(enc), ("utf-8" if enc == "utf-8-sig" else enc)
        except UnicodeDecodeError:
            continue
    # latin-1 能解码任意字节，作为兜底（可能出现乱码，前端预览可见）
    return file_bytes.decode("latin-1"), "latin-1"


def read_csv(
    file_bytes: bytes,
    has_header: bool = True,
    header_row: int = 1,
    data_start_row: int | None = None,
    skip_trailing_rows: int = 0,
    combine_header_rows: int = 1,
) -> ParsedCsv:
    """解析 CSV 原始字节，返回表头、数据行以及实际使用的编码/分隔符/行号。"""
    text_content, encoding = detect_encoding(file_bytes)
    logger.info("CSV 编码: %s", encoding)
    if not text_content.strip():
        raise ValueError("CSV 文件为空。")
    delimiter = _detect_delimiter(text_content)
    reader = csv.reader(io.StringIO(text_content, newline=""), delimiter=delimiter)
    return read_rows(list(reader), encoding, delimiter, has_header, header_row, data_start_row,
                     skip_trailing_rows, combine_header_rows)


def read_rows(
    raw_rows: list[list[str]],
    encoding: str,
    delimiter: str,
    has_header: bool = True,
    header_row: int = 1,
    data_start_row: int | None = None,
    skip_trailing_rows: int = 0,
    combine_header_rows: int = 1,
) -> ParsedCsv:
    """
    表格行 → 表头 + 数据行（CSV 与 Excel 共用）：自动检测表头行、合并多行表头、跳过末尾非数据行。
    行号均为过滤空行后的逻辑行号（1-based）。
    """
    combine_header_rows = max(1, combine_header_rows)
    logical_rows = [row for row in raw_rows if any(str(c).strip() for c in row)]
    if not logical_rows:
        raise ValueError("文件没有有效内容。")

    total_lines = len(logical_rows)

    # ── 跳过末尾行 ──
    if skip_trailing_rows > 0:
        if skip_trailing_rows >= len(logical_rows):
            raise ValueError(f"跳过末尾行数({skip_trailing_rows})≥总行数({len(logical_rows)})")
        logical_rows = logical_rows[:len(logical_rows) - skip_trailing_rows]
    total_rows = len(logical_rows)

    # ── 自动检测表头行 ──
    if has_header and header_row <= 0:
        header_row = auto_detect_header_row(logical_rows)
        logger.info("自动检测表头行: %d", header_row)
    header_row = max(1, header_row)

    if data_start_row is None or data_start_row <= 0:
        data_start_row = header_row + combine_header_rows if has_header else 1

    # ── 提取表头 ──
    if has_header:
        h_idx = header_row - 1  # 转0-based
        if h_idx + combine_header_rows > total_rows:
            raise ValueError(f"表头行范围超出总行数({total_rows})，请调整行号设置。")

        if combine_header_rows == 1:
            headers = [h.strip() for h in logical_rows[h_idx]]
        else:
            header_rows = logical_rows[h_idx:h_idx + combine_header_rows]
            max_c = max((len(r) for r in header_rows), default=0)
            headers = []
            for ci in range(max_c):
                parts = []
                for row in header_rows:
                    v = row[ci].strip() if ci < len(row) else ""
                    if v and v not in parts:
                        parts.append(v)
                headers.append("-".join(parts) if parts else f"列{ci + 1}")
            logger.info("合并%d行表头→%d列", combine_header_rows, len(headers))

        d_idx = max(data_start_row - 1, h_idx + combine_header_rows)
        if d_idx >= total_rows:
            raise ValueError(f"数据起始行 {data_start_row} 超出范围({total_rows})")
        rows = logical_rows[d_idx:]
        data_start_row = d_idx + 1
    else:
        max_c = max((len(row) for row in logical_rows), default=0)
        headers = [f"col_{i + 1}" for i in range(max_c)]
        rows = logical_rows[data_start_row - 1:]

    if not rows:
        raise ValueError("没有找到数据行，请检查「列名所在行」和「数据起始行」设置。")

    # 数据行比表头宽时补齐表头，避免静默丢列
    widest = max(len(row) for row in rows)
    for ci in range(len(headers), widest):
        if any(ci < len(row) and row[ci].strip() for row in rows):
            headers.append(f"列{ci + 1}")
    max_cols = len(headers)
    rows = [_pad_row(row, max_cols) for row in rows]

    logger.info("CSV解析: %d列 %d行 (表头=%d~%d 数据起始=%d 跳末尾=%d)",
                max_cols, len(rows), header_row, header_row + combine_header_rows - 1,
                data_start_row, skip_trailing_rows)
    return ParsedCsv(
        headers=headers,
        rows=rows,
        encoding=encoding,
        delimiter=delimiter,
        header_row=header_row if has_header else 0,
        data_start_row=data_start_row,
        total_lines=total_lines,
    )


def parse_csv(file_bytes: bytes, **kwargs) -> tuple[list[str], list[list[str]]]:
    """兼容旧接口：只返回 (表头列表, 数据行列表)。"""
    parsed = read_csv(file_bytes, **kwargs)
    return parsed.headers, parsed.rows


def _detect_delimiter(text: str) -> str:
    sample = "\n".join(text.strip().split("\n")[:10])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except Exception:
        counts = {d: sample.count(d) for d in [",", "\t", ";", "|"]}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def _pad_row(row: list[str], target_len: int) -> list[str]:
    if len(row) < target_len:
        return row + [""] * (target_len - len(row))
    return row[:target_len]


def auto_detect_trailing_rows(rows: list[list[str]], threshold: float = 0.5) -> int:
    """自动检测末尾非数据行数量（基于关键词匹配）"""
    if len(rows) < 3:
        return 0
    n = 0
    for row in reversed(rows[-10:]):
        row_text = "".join(str(c) for c in row)
        matches = sum(1 for kw in _FOOTER_KEYWORDS if kw in row_text)
        if matches >= 2 or (matches >= 1 and len([c for c in row if c.strip()]) <= 3):
            n += 1
        else:
            break
    return min(n, 10)


def auto_detect_header_row(logical_rows: list[list[str]]) -> int:
    """自动检测表头所在逻辑行号（1-based）"""
    # 策略：找第一个包含「序号」或「学号」或「姓名」等常见表头关键词的行
    _header_keywords = ["序号", "学号", "姓名", "编号", "ID", "id", "代码", "日期"]
    for i, row in enumerate(logical_rows[:15]):
        row_text = " ".join(str(c)[:20] for c in row if c.strip())
        matches = sum(1 for kw in _header_keywords if kw in row_text)
        # 同时检查第一列是否为数字（表头行第一列通常是文本如"序号"）
        first_col = row[0].strip() if row else ""
        is_text_header = first_col and not first_col.replace(".", "").replace("-", "").isdigit()
        if matches >= 2 or (matches >= 1 and is_text_header):
            return i + 1  # 1-based
    return 1  # 默认第一行


# ═══════════════════════════════════════════════════════════════
# 2. 列名规范化
# ═══════════════════════════════════════════════════════════════

def sanitize_column_name(raw: str, used_names: set | None = None) -> str:
    if used_names is None:
        used_names = set()
    name = str(raw).strip()
    name = re.sub(r"[\x00-\x1f`\"]", "", name)
    name = name.replace("\u3000", " ")
    name = re.sub(r" {2,}", " ", name).strip()
    if not name:
        name = f"column_{len(used_names) + 1}"
    if name[0].isdigit():
        name = "col_" + name
    if name.lower() in RESERVED_COLUMN_NAMES:
        name = "src" + name
    name = truncate_identifier(name)
    original = name
    counter = 2
    lowered = {n.lower() for n in used_names}
    while name.lower() in lowered:
        suffix = f"_{counter}"
        name = truncate_identifier(original, 63 - len(suffix)) + suffix
        counter += 1
    used_names.add(name)
    return name


# ═══════════════════════════════════════════════════════════════
# 3. 表名推导
# ═══════════════════════════════════════════════════════════════

def normalize_table_name(name: str) -> str:
    """把任意输入规范化为 csv_ 前缀、仅含字母数字下划线/中文、≤63 字节的表名。"""
    name = re.sub(r"[^\w]", "_", str(name).strip())
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "imported"
    if not name.lower().startswith("csv_"):
        name = "csv_" + name
    return truncate_identifier(name).rstrip("_")


def derive_table_name(filename: str) -> str:
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    return normalize_table_name(name)


# ═══════════════════════════════════════════════════════════════
# 4. 类型推断
# ═══════════════════════════════════════════════════════════════

def normalize_column_type(column_type: str) -> str:
    """校验列类型是否在白名单内，返回规范化后的大写类型；不合法则抛 ValueError。"""
    normalized = re.sub(r"\s+", " ", str(column_type).strip()).upper()
    if not any(p.match(normalized) for p in _ALLOWED_TYPE_PATTERNS):
        raise ValueError(f"不支持的列类型: {column_type!r}")
    return normalized


def infer_column_types(headers: list[str], rows: list[list[str]], overrides: dict | None = None) -> list[dict]:
    overrides = {k: normalize_column_type(v) for k, v in (overrides or {}).items()}
    if not rows:
        return [{"name": h, "type": overrides.get(h, f"VARCHAR({_MIN_VARCHAR_LEN})"),
                 "nullable": True, "max_len": _MIN_VARCHAR_LEN} for h in headers]

    results = []
    for col_idx, col_name in enumerate(headers):
        if col_name in overrides:
            results.append({"name": col_name, "type": overrides[col_name], "nullable": True, "max_len": _MIN_VARCHAR_LEN})
            continue

        values = [row[col_idx] if col_idx < len(row) else "" for row in rows]
        non_null = [v.strip() for v in values if v.strip().lower() not in _NULL_MARKERS]
        has_null = len(non_null) < len(values)
        max_blen = max((len(v) for v in non_null), default=0)  # PostgreSQL VARCHAR(n) 按字符计数

        if not non_null:
            results.append({"name": col_name, "type": f"VARCHAR({_MIN_VARCHAR_LEN})", "nullable": True, "max_len": _MIN_VARCHAR_LEN})
            continue

        is_int, max_int_val = _try_int(non_null)
        if is_int:
            int_type = "BIGINT" if max_int_val > _INT_MAX else "INT"
            results.append({"name": col_name, "type": int_type, "nullable": has_null, "max_len": max_blen})
        elif (dt := _try_decimal(non_null)):
            results.append({"name": col_name, "type": dt, "nullable": has_null, "max_len": max_blen})
        elif _try_date(non_null):
            results.append({"name": col_name, "type": "DATE", "nullable": has_null, "max_len": max_blen})
        elif _try_datetime(non_null):
            results.append({"name": col_name, "type": "TIMESTAMP", "nullable": has_null, "max_len": max_blen})
        elif max_blen * 2 > _MAX_VARCHAR_LEN:
            results.append({"name": col_name, "type": "TEXT", "nullable": has_null, "max_len": max_blen})
        else:
            vlen = max(max_blen * 2, _MIN_VARCHAR_LEN)  # 预留一倍余量，便于后续追加数据
            results.append({"name": col_name, "type": f"VARCHAR({vlen})", "nullable": has_null, "max_len": vlen})

    return results


def profile_import_file(
    filename: str,
    headers: list[str],
    clean_headers: list[str],
    rows: list[list[str]],
    column_types: list[dict],
    encoding: str,
    delimiter: str,
) -> dict:
    """Infer import intent, likely primary keys, semantic columns, and quality hints."""
    lower_name = filename.lower()
    file_type = "csv" if lower_name.endswith(".csv") else "unknown"
    total_rows = len(rows)
    total_cols = len(clean_headers)

    primary_key_candidates = []
    for idx, col in enumerate(clean_headers):
        values = [row[idx].strip() for row in rows if idx < len(row)]
        non_empty = [v for v in values if v.strip().lower() not in _NULL_MARKERS]
        unique_count = len(set(non_empty))
        uniqueness = unique_count / len(non_empty) if non_empty else 0
        score = 0
        normalized = col.lower()
        id_like = False
        if normalized in {"id", "_id"} or normalized.endswith("_id"):
            id_like = True
            score += 0.35
        if any(token in col for token in ("编号", "编码", "代码", "单号", "学号", "工号", "ID", "id")):
            id_like = True
            score += 0.35
        first_column_unique = idx == 0 and uniqueness >= 0.98 and len(non_empty) == total_rows
        if uniqueness >= 0.98 and len(non_empty) == total_rows:
            score += 0.45
        if first_column_unique and not id_like:
            score = max(score, 0.55)
        score = min(score, 1.0)
        if id_like or first_column_unique:
            primary_key_candidates.append({
                "column": col,
                "score": round(score, 2),
                "unique_count": unique_count,
                "non_empty_count": len(non_empty),
                "reason": "字段名像主键且值基本唯一" if uniqueness >= 0.98 else "字段名像主键",
            })

    primary_key_candidates.sort(key=lambda item: item["score"], reverse=True)

    semantic_columns = []
    for col, col_type in zip(clean_headers, column_types):
        semantic = _guess_semantic_type(col, col_type["type"])
        if semantic:
            semantic_columns.append({
                "column": col,
                "semantic_type": semantic,
                "db_type": col_type["type"],
            })

    empty_cells = 0
    total_cells = max(total_rows * total_cols, 1)
    duplicate_rows = 0
    seen_rows = set()
    for row in rows:
        normalized_row = tuple(row[:total_cols])
        if normalized_row in seen_rows:
            duplicate_rows += 1
        seen_rows.add(normalized_row)
        empty_cells += sum(1 for cell in row[:total_cols] if cell.strip().lower() in _NULL_MARKERS)

    warnings = []
    if not primary_key_candidates:
        warnings.append("未发现高置信主键列，导入时将使用自动行号 _id。")
    if duplicate_rows:
        warnings.append(f"检测到 {duplicate_rows} 行重复记录。")
    if empty_cells / total_cells > 0.15:
        warnings.append("空值比例较高，建议导入前确认缺失值含义。")
    if total_cols > 30:
        warnings.append("列数较多，建议确认是否存在多行表头或宽表拆分需求。")

    return {
        "file_type": file_type,
        "encoding": encoding,
        "delimiter": delimiter,
        "row_count": total_rows,
        "column_count": total_cols,
        "primary_key_candidates": primary_key_candidates[:3],
        "recommended_primary_key": primary_key_candidates[0]["column"] if primary_key_candidates else None,
        "semantic_columns": semantic_columns,
        "quality": {
            "empty_cell_ratio": round(empty_cells / total_cells, 4),
            "duplicate_rows": duplicate_rows,
        },
        "warnings": warnings,
        "suggested_table_name": derive_table_name(filename),
    }


def _guess_semantic_type(column_name: str, db_type: str) -> str | None:
    name = column_name.lower()
    if any(token in column_name for token in ("日期", "时间")) or "date" in name or "time" in name:
        return "date_or_time"
    if any(token in column_name for token in ("金额", "销售额", "收入", "价格", "费用", "成本")) or any(token in name for token in ("amount", "price", "cost", "sales")):
        return "money"
    if any(token in column_name for token in ("数量", "人数", "次数", "库存")) or any(token in name for token in ("count", "qty", "quantity", "stock")):
        return "quantity"
    if any(token in column_name for token in ("地区", "省", "市", "城市", "区域")) or any(token in name for token in ("region", "province", "city")):
        return "geo"
    if any(token in column_name for token in ("渠道", "来源")) or "channel" in name:
        return "channel"
    if any(token in column_name for token in ("类别", "品类", "类型", "状态")) or any(token in name for token in ("category", "type", "status")):
        return "category"
    if "INT" in db_type or "DECIMAL" in db_type or "NUMERIC" in db_type:
        return "numeric"
    return None


_INT_PATTERN = re.compile(r"^[+-]?\d+$")
_DECIMAL_PATTERN = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")
_DATE_PATTERN = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_DATETIME_PATTERN = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?$")


def _has_leading_zero(v: str) -> bool:
    digits = v.lstrip("+-")
    return len(digits) > 1 and digits.startswith("0")


def _try_int(vals):
    """尝试 INT / BIGINT。带前导零的值（学号、邮编、工号）保留为文本，避免 007 → 7。"""
    max_val = 0
    for v in vals:
        if not _INT_PATTERN.match(v) or _has_leading_zero(v):
            return False, 0
        max_val = max(max_val, abs(int(v)))
    if max_val > _BIGINT_MAX:  # 身份证号等超长数字串
        return False, 0
    return True, max_val


def _try_decimal(vals):
    mi, mf = 0, 0
    for v in vals:
        if not _DECIMAL_PATTERN.match(v):
            return None
        if _has_leading_zero(v.split(".", 1)[0]):
            return None
        ip, _, fp = v.lstrip("+-").partition(".")
        mi = max(mi, len(ip.lstrip("0")) or 1)
        mf = max(mf, len(fp))
    if mf == 0:
        return None  # 全是整数却没通过 _try_int（超出 BIGINT）：多半是证件号/流水号，按文本存
    scale = min(mf, 6)
    precision = mi + scale
    if precision > 38:
        return "NUMERIC"
    return f"DECIMAL({max(precision, 1)},{scale})"


def _try_date(vals):
    for v in vals:
        m = _DATE_PATTERN.match(v)
        if not m:
            return False
        try:
            datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return False
    return True


def _try_datetime(vals):
    for v in vals:
        if not _DATETIME_PATTERN.match(v):
            return False
        try:
            datetime.datetime.fromisoformat(v.replace("/", "-"))
        except ValueError:
            return False
    return True


# ═══════════════════════════════════════════════════════════════
# 5. 建表 & 写入
# ═══════════════════════════════════════════════════════════════

def build_create_table_sql(table_name: str, columns: list[dict], primary_key_column: str | None = None) -> str:
    col_defs = []
    has_custom_pk = bool(primary_key_column) and any(c["name"] == primary_key_column for c in columns)
    if not has_custom_pk:
        col_defs.append('"_id" INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY')
    for col in columns:
        col_type = normalize_column_type(col["type"])
        if has_custom_pk and col["name"] == primary_key_column:
            col_defs.append(f"{quote_identifier(col['name'])} {col_type} NOT NULL PRIMARY KEY")
        else:
            null_clause = "NULL" if col.get("nullable", True) else "NOT NULL"
            col_defs.append(f"{quote_identifier(col['name'])} {col_type} {null_clause}")
    col_defs.append('"_imported_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP')
    return f"CREATE TABLE {quote_identifier(table_name)} (\n  " + ",\n  ".join(col_defs) + "\n)"


def _row_params(row: list[str], width: int) -> dict:
    return {
        f"col_{idx}": (value.strip() if value.strip().lower() not in _NULL_MARKERS else None)
        for idx, value in enumerate(row[:width])
    }


def import_table(
    table_name: str,
    columns: list[dict],
    headers: list[str],
    rows: list[list[str]],
    primary_key_column: str | None = None,
    overwrite: bool = False,
    batch_size: int = 500,
) -> int:
    """
    在单个事务中完成：(可选)删除旧表 → 建表 → 分批写入。

    任何一步失败都会整体回滚 —— 旧表不会被误删，也不会留下只写了一半的新表。
    """
    ddl = build_create_table_sql(table_name, columns, primary_key_column)
    logger.info("DDL: %s", ddl[:300])
    cs = ", ".join(quote_identifier(h) for h in headers)
    ph = ", ".join(f":col_{i}" for i in range(len(headers)))
    insert_sql = text(f"INSERT INTO {quote_identifier(table_name)} ({cs}) VALUES ({ph})")

    total = 0
    with get_connection() as conn:
        if overwrite:
            drop_table(table_name, conn=conn)
        conn.execute(text(ddl))
        for i in range(0, len(rows), batch_size):
            batch = [_row_params(row, len(headers)) for row in rows[i:i + batch_size]]
            conn.execute(insert_sql, batch)
            total += len(batch)
            logger.info("已插入 %d/%d 行 → %s", total, len(rows), table_name)
    return total
