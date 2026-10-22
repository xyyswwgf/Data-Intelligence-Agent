"""
=============================================================================
SQL 安全过滤器 — 纵深防御的第二道防线
=============================================================================

安全哲学:
  永远不要信任 LLM 的输出。虽然我们通过 System Prompt 要求模型只生成
  SELECT 语句，但大模型可能被 prompt injection 诱导而输出危险 SQL。
  因此，所有 LLM 生成的 SQL 必须经过本模块的二次校验。

防护层级:
  Layer 1: LLM System Prompt 约束（"只生成 SELECT"）
  Layer 2: 本模块正则白名单校验（"只放行 SELECT"）
  Layer 3: database.execute_readonly_sql —— READ ONLY 事务 + 超时 + 行数上限

拦截范围:
  - DDL: DROP, ALTER, CREATE, TRUNCATE, RENAME
  - DML: DELETE, UPDATE, INSERT, REPLACE, MERGE
  - DCL: GRANT, REVOKE
  - 其他: SELECT INTO（PostgreSQL 中会建表）, COPY, EXEC, SLEEP, BENCHMARK
  - PostgreSQL 危险函数: pg_sleep, pg_read_file, pg_ls_dir, lo_import, dblink, set_config 等
  - 语句堆叠: 检测多个分号

实现要点:
  关键字扫描前先把字符串字面量和双引号标识符替换为占位符，
  避免 WHERE status = 'UPDATE' 或列名 "update" 被误杀，
  也避免攻击者把分号藏进字符串里干扰计数。
=============================================================================
"""

import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """SQL 安全校验结果"""

    is_safe: bool
    reason: str


# ---------------------------------------------------------------------------
# 危险关键字模式库
# ---------------------------------------------------------------------------
# 每个元素是正则表达式，\b 确保匹配完整单词而非子串
# 例如 \bDROP\b 会匹配 "DROP TABLE" 但不会匹配 "DROPDOWN"

_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(pattern), description)
    for pattern, description in [
        # ── DDL（数据定义语言）— 结构变更 ──
        (r"\bDROP\b", "DROP（删除表/库/索引）"),
        (r"\bALTER\b", "ALTER（修改表结构）"),
        (r"\bCREATE\b", "CREATE（创建表/库）"),
        (r"\bTRUNCATE\b", "TRUNCATE（清空表数据）"),
        (r"\bRENAME\b", "RENAME（重命名）"),
        # ── DML（数据操作语言）— 数据变更 ──
        (r"\bDELETE\b", "DELETE（删除数据行）"),
        (r"\bUPDATE\b", "UPDATE（更新数据）"),
        (r"\bINSERT\b", "INSERT（插入数据）"),
        (r"\bREPLACE\b", "REPLACE（替换数据）"),
        (r"\bMERGE\b", "MERGE（合并数据）"),
        (r"\bINTO\b", "SELECT INTO / INTO OUTFILE（建表或写文件）"),
        (r"\bCOPY\b", "COPY（读写服务器文件）"),
        # ── DCL（数据控制语言）— 权限变更 ──
        (r"\bGRANT\b", "GRANT（授权）"),
        (r"\bREVOKE\b", "REVOKE（撤销权限）"),
        # ── 危险函数 / 文件操作 ──
        (r"\bLOAD\b", "LOAD（加载数据/文件）"),
        (r"\bEXEC\b", "EXEC（执行命令）"),
        (r"\bEXECUTE\b", "EXECUTE（执行命令）"),
        (r"\bCALL\b", "CALL（调用存储过程）"),
        (r"\bSLEEP\b", "SLEEP（延时函数，常用于盲注）"),
        (r"\bBENCHMARK\b", "BENCHMARK（性能测试函数，常用于盲注）"),
        (r"\bPG_SLEEP(_FOR|_UNTIL)?\b", "pg_sleep（延时函数，常用于盲注）"),
        (
            r"\bPG_(READ_FILE|READ_BINARY_FILE|LS_DIR|STAT_FILE|LS_LOGDIR|LS_WALDIR)\b",
            "pg_read_file / pg_ls_dir（读取服务器文件）",
        ),
        (r"\bLO_(IMPORT|EXPORT|UNLINK|CREATE|PUT|FROM_BYTEA)\b", "Large Object 文件操作"),
        (r"\bDBLINK\w*\b", "dblink（跨库连接）"),
        (
            r"\bPG_(TERMINATE_BACKEND|CANCEL_BACKEND|RELOAD_CONF|ROTATE_LOGFILE|PROMOTE)\b",
            "服务器管理函数",
        ),
        (r"\bSET_CONFIG\b", "set_config（修改会话配置）"),
        (r"\bNEXTVAL\b|\bSETVAL\b", "序列写操作"),
    ]
]

# SELECT 语句特征模式 — 允许 SELECT、EXPLAIN SELECT、括号开头的 UNION 和 PostgreSQL CTE
_SELECT_HEAD_PATTERN = re.compile(
    r"^\(*\s*(SELECT\b|EXPLAIN\s+SELECT\b|WITH\b[\s\S]+\bSELECT\b)",
    re.IGNORECASE,
)

# 注释清理模式
_COMMENT_SINGLE_LINE = re.compile(r"--[^\n]*")
_COMMENT_MULTI_LINE = re.compile(r"/\*.*?\*/", re.DOTALL)

# 字面量 / 标识符：'...'（'' 转义）、E'...'、$tag$...$tag$、"..."（"" 转义）
_STRING_LITERAL = re.compile(r"[Ee]?'(?:[^']|'')*'")
_DOLLAR_QUOTED = re.compile(r"\$([A-Za-z_]\w*|)\$[\s\S]*?\$\1\$")
_QUOTED_IDENTIFIER = re.compile(r'"(?:[^"]|"")*"')


def _mask_literals(sql: str) -> str:
    """把字面量和带引号的标识符替换成中性占位符，只留下 SQL 骨架用于安全扫描。"""
    masked = _DOLLAR_QUOTED.sub(" '_' ", sql)
    masked = _STRING_LITERAL.sub(" '_' ", masked)
    masked = _QUOTED_IDENTIFIER.sub(' "_" ', masked)
    return masked


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    校验 SQL 语句是否安全（只允许 SELECT）。

    参数:
        sql: 待校验的 SQL 字符串

    返回:
        (is_safe, reason) 二元组
        - is_safe=True  表示通过校验，可以执行
        - is_safe=False 表示被拦截，reason 说明原因

    示例:
        >>> validate_sql("SELECT * FROM orders")
        (True, 'OK')

        >>> validate_sql("DROP TABLE orders")[0]
        False

        >>> validate_sql("SELECT 1; DROP TABLE orders;")[0]
        False
    """
    # ── 空值检查 ──
    if not sql or not sql.strip():
        return False, "SQL 语句为空"

    # ── 字面量屏蔽 + 注释清理 ──
    # 先屏蔽字面量，再去注释：字符串里的 "--" 不会被误当成注释，
    # 注释里的关键字和分号也不会参与后续判断
    skeleton = _mask_literals(sql.strip())
    skeleton = _COMMENT_MULTI_LINE.sub(" ", skeleton)
    skeleton = _COMMENT_SINGLE_LINE.sub(" ", skeleton)
    skeleton = skeleton.strip()

    # 未闭合的引号说明 SQL 结构异常（或试图干扰解析），直接拒绝
    if "'" in _STRING_LITERAL.sub("", skeleton) or '"' in _QUOTED_IDENTIFIER.sub("", skeleton):
        return False, "SQL 中存在未闭合的引号"

    # ── 校验 1: 必须以 SELECT 开头 ──
    if not _SELECT_HEAD_PATTERN.match(skeleton):
        return (
            False,
            "只允许执行 SELECT 查询语句。"
            "检测到非 SELECT 开头（可能包含 WITH/EXPLAIN 以外的前缀）。",
        )

    # ── 校验 2: 禁止危险关键字 ──
    upper_sql = skeleton.upper()
    for pattern, description in _DANGEROUS_PATTERNS:
        if pattern.search(upper_sql):
            return (
                False,
                f"SQL 包含被禁止的操作: {description}",
            )

    # ── 校验 3: 分号堆叠注入检测 ──
    # 允许末尾一个分号（或无分号），但中间出现分号意味着语句堆叠
    # 例如: SELECT 1; DROP TABLE orders; -- 这是两条语句
    body = skeleton.rstrip().rstrip(";").rstrip()
    if ";" in body:
        return (
            False,
            f"SQL 包含 {skeleton.count(';')} 个分号，可能存在语句堆叠注入。"
            "只允许末尾一个分号或不带分号。",
        )

    return True, "OK"


def strip_trailing_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()
