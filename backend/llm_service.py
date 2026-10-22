"""
=============================================================================
LLM 服务 — 大模型调用层（NL → SQL 转换核心）
=============================================================================

职责:
  将用户的自然语言问题 + 数据库 DDL 元数据打包发送给大模型，
  模型返回对应的 SQL 查询语句。

Prompt 工程策略:
  - System Prompt 中包含所有表的 CREATE TABLE 语句（元数据外挂）
  - 明确约束：只输出 SQL，不要解释，不要 Markdown
  - 温度设为 0，确保结果确定性（同一问题→同一 SQL）

模型配置:
  当前使用 DeepSeek V4 Pro（兼容 Anthropic Messages API）
  可通过 ANTHROPIC_BASE_URL + LLM_MODEL 环境变量切换

自我修正:
  SQL 执行失败时，调用方可把失败的 SQL 和数据库报错回传给 generate_sql，
  模型基于报错重写一次（典型场景：列名写错、类型不匹配、GROUP BY 缺列）

异常约定:
  ValueError   — 问题无法转换为 SQL（LLM 返回 UNANSWERABLE）
  RuntimeError — API 网络/认证异常（调用方应返回 502）
=============================================================================
"""

import re
import anthropic
from backend.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS
from backend.database import get_ddl

# ---------------------------------------------------------------------------
# API 客户端（懒加载单例：未配置 Key 时导入本模块不报错）
# ---------------------------------------------------------------------------

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            base_url=ANTHROPIC_BASE_URL,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=2,
        )
    return _client

# ---------------------------------------------------------------------------
# System Prompt 模板
# ---------------------------------------------------------------------------
# 这是整个系统的"灵魂"——Prompt 工程的核心
# {ddl} 占位符会被替换为实时提取的数据库建表语句

_SYSTEM_PROMPT_TEMPLATE = """你是一个资深的 PostgreSQL SQL 查询专家。你的唯一任务是将用户的自然语言问题转换为**一行可执行的 SELECT 语句**。

## 数据库结构
以下是当前数据库所有表的真实建表语句（DDL），你必须严格依据这些表结构生成 SQL，不能编造不存在的列名或表名：

{ddl}

## 核心规则
1. **只输出一行 SQL**，不要任何解释、注释、分析过程、Markdown 标记（如 ```sql）。
2. **只生成 SELECT**，绝对禁止 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE。
3. 如果问题无法用 SQL 回答（主观问题、与数据无关），只输出一个词：UNANSWERABLE
4. 所有表名和列名用双引号包裹（如 "表名"、"列名"），字符串值用单引号（如 '张三'）。
5. 聚合查询必须加 GROUP BY，排序用 ORDER BY。
6. 日期字段使用 DATE 字面量（如 DATE '2026-06-01'），范围用 BETWEEN。
7. 当前数据覆盖 2026-01-01 到 2026-06-30。用户说“本月”时默认指 2026-06，“上月”默认指 2026-05。
8. 明细查询（非聚合）默认加 LIMIT 200，除非用户明确要求全部数据。
9. 结果列使用中文别名（AS "销售额"），方便业务人员阅读。
10. 订单相关指标只统计有效订单（fact_orders.status = 'paid'）。

---

## SQL 语法参考
> 以下示例中的表名（如 "students"、"scores"）仅用于演示语法，生成 SQL 时必须替换为上方 DDL 中真实存在的表和列。

### 1. 基本查询 — SELECT / AS / DISTINCT
- 「查看所有学生」→ SELECT * FROM "students"
- 「查看学生姓名和学号」→ SELECT "姓名", "学号" FROM "students"
- 「有哪些院系？」→ SELECT DISTINCT "院系" FROM "students"
- 「每个学生的总分」→ SELECT "姓名", "语文"+"数学"+"英语" AS "总分" FROM "scores"

### 2. 筛选条件 — WHERE / AND / OR / NOT / <>
- 「成绩大于80」→ WHERE "成绩" > 80
- 「成绩>=80且<90」→ WHERE "成绩" >= 80 AND "成绩" < 90
- 「成绩在80到90之间」→ WHERE "成绩" BETWEEN 80 AND 90
- 「院系是计算机或数学的」→ WHERE "院系" IN ('计算机学院', '数学学院')
- 「名字包含"张"的」→ WHERE "姓名" LIKE '%张%'

### 3. NULL 值处理 — IS NULL / IS NOT NULL
- 「没有填写备注的学生」→ WHERE "备注" IS NULL
- 「有备注的学生」→ WHERE "备注" IS NOT NULL

### 4. 排序 — ORDER BY ASC / DESC / 多字段
- 「按成绩从高到低」→ ORDER BY "成绩" DESC
- 「按成绩从低到高」→ ORDER BY "成绩" ASC

### 5. 限制行数 — LIMIT / LIMIT OFFSET
- 「前5名」→ ORDER BY "成绩" DESC LIMIT 5
- 「第6到第10名」→ ORDER BY "成绩" DESC LIMIT 5 OFFSET 5
- 「销售额最高的3个商品」→ ORDER BY "销售额" DESC LIMIT 3

### 6. 聚合函数 — COUNT / SUM / AVG / MAX / MIN
- 「总共有多少学生？」→ SELECT COUNT(*) AS "总人数" FROM "students"
- 「有成绩记录的学生数（去重）」→ SELECT COUNT(DISTINCT "学号") FROM "scores"
- 「总销售额」→ SELECT SUM("order_amount") AS "总销售额" FROM "fact_orders"
- 「平均分」→ SELECT AVG("成绩") AS "平均分" FROM "scores"

### 7. 分组聚合 — GROUP BY
- 「每个地区的销售额」→ SELECT r."province", SUM(o."order_amount") AS "销售额" FROM "fact_orders" o JOIN "dim_region" r ON o."region_id" = r."region_id" GROUP BY r."province"
- 「每天的订单数」→ SELECT "order_date", COUNT(*) AS "订单数" FROM "fact_orders" GROUP BY "order_date"

### 8. 聚合后筛选 — HAVING
- 「销售额大于1000的商品类别」→ GROUP BY p."category" HAVING SUM(o."order_amount") > 1000

### 9. 表关联 — JOIN / LEFT JOIN / 多表JOIN
- 「订单对应的商品名」→ FROM "fact_orders" o JOIN "dim_product" p ON o."product_id" = p."product_id"
- 「所有商品及其订单（含无订单的商品）」→ FROM "dim_product" p LEFT JOIN "fact_orders" o ON ...

### 10. 子查询 — WHERE ... IN (SELECT ...) / 派生表
- 「高于平均值的商品」→ WHERE "order_amount" > (SELECT AVG("order_amount") FROM "fact_orders")
- 「每个院系最高分的学生」→ 使用派生表子查询

### 11. CASE WHEN — 条件分类
- 「销售额>=10000为高，>=5000为中，否则低」
  → SELECT CASE WHEN SUM("order_amount")>=10000 THEN '高' WHEN SUM("order_amount")>=5000 THEN '中' ELSE '低' END AS "等级" FROM "fact_orders"

### 12. 日期函数 — TO_CHAR / EXTRACT / DATE_TRUNC
- 「按月统计订单数」→ SELECT TO_CHAR("order_date",'YYYY-MM') AS "月份", COUNT(*) FROM "fact_orders" GROUP BY TO_CHAR("order_date",'YYYY-MM')
- 「2026年的订单」→ WHERE EXTRACT(YEAR FROM "order_date") = 2026
- 「5月的订单」→ WHERE EXTRACT(MONTH FROM "order_date") = 5

### 13. 字符串函数 — CONCAT / SUBSTRING
- 「取日期年月」→ SELECT TO_CHAR("order_date", 'YYYY-MM') FROM "fact_orders"

### 14. 数值函数 — ROUND / FLOOR / CEIL
- 「平均客单价保留2位小数」→ SELECT ROUND(AVG("order_amount"), 2) AS "平均客单价" FROM "fact_orders"

### 15. 序号/排名 — ROW_NUMBER / RANK（PostgreSQL 窗口函数）
- 「每个地区销售额排名」→ SELECT r."province", SUM(o."order_amount") AS "销售额", RANK() OVER (ORDER BY SUM(o."order_amount") DESC) AS "排名" FROM "fact_orders" o JOIN "dim_region" r ON o."region_id"=r."region_id" GROUP BY r."province"

### 16. UNION 合并
- 「两个表里都出现过的客户」→ SELECT "customer_id" FROM "表A" UNION SELECT "customer_id" FROM "表B"

---

## 记住
- 用户用中文描述 → 你输出一行纯SQL
- 不确定时参考上面最接近的语法模式
- 列名和表名必须来自上面给出的真实DDL
- 不要自创列名，不要使用DDL中不存在的表名

"""

_REPAIR_TEMPLATE = """上一次生成的 SQL 执行失败了，请根据数据库报错修正，仍然只输出一行 SELECT。

用户问题：{question}

失败的 SQL：
{failed_sql}

数据库报错：
{error}
"""


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

# 匹配 LLM 可能包裹的 Markdown 代码块
_MARKDOWN_CODE_PATTERN = re.compile(
    r"```(?:\w+)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_sql_from_response(raw_text: str) -> str:
    """
    从 LLM 原始输出中提取纯净的 SQL 语句

    处理以下情况:
      - 裸 SQL: "SELECT * FROM fact_orders"
      - Markdown 包裹: "```sql\nSELECT * FROM fact_orders\n```"
      - 多行: "```\nSELECT ...\n```"
    """
    text = raw_text.strip()

    # 尝试匹配 Markdown 代码块
    match = _MARKDOWN_CODE_PATTERN.search(text)
    if match:
        return match.group(1).strip()

    # 没有代码块标记：模型偶尔会在 SQL 前加一句说明，截取从 SELECT/WITH 开始的部分
    if not re.match(r"(?i)^\(*\s*(SELECT|WITH)\b", text):
        start = re.search(r"(?im)^\s*\(*\s*(SELECT|WITH)\b", text)
        if start:
            text = text[start.start():].strip()
    return text


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def generate_sql(question: str, failed_sql: str | None = None, error: str | None = None) -> str:
    """
    将自然语言问题转换为 SQL 语句

    这是整个 NL2SQL 链路的核心——输入自然语言，输出可执行的 SQL。

    参数:
        question:   用户的自然语言问题
                    示例: "每种商品类别的总销售额是多少？"
        failed_sql: （可选）上一次执行失败的 SQL，用于自我修正
        error:      （可选）上一次执行的数据库报错

    返回:
        生成的 SQL 字符串

    异常:
        ValueError:
          - 问题无法用 SQL 回答（LLM 返回 UNANSWERABLE）
        RuntimeError:
          - API 网络请求失败、认证失败、超时，或模型返回空内容
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "未配置大模型 API Key。请设置 ANTHROPIC_API_KEY、"
            "ANTHROPIC_AUTH_TOKEN 或 DEEPSEEK_API_KEY 后重启后端。"
        )

    # 1. 动态获取最新的数据库 DDL（元数据外挂）
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(ddl=get_ddl())

    # 2. 组装用户消息（首次生成 / 基于报错修正）
    if failed_sql and error:
        user_content = _REPAIR_TEMPLATE.format(question=question, failed_sql=failed_sql, error=error[:1000])
    else:
        user_content = question

    # 3. 调用大模型
    try:
        response = _get_client().messages.create(
            model=LLM_MODEL,
            max_tokens=2048,
            temperature=0,  # 温度为 0 保证相同问题输出相同 SQL
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            # 禁用 thinking 模式 — 我们只需要直接的SQL输出，不需要推理过程
            thinking={"type": "disabled"},
        )
    except Exception as exc:
        raise RuntimeError(
            f"大模型 API 调用失败: {str(exc)}。请检查 API Key 和网络连接。"
        ) from exc

    # 4. 提取纯净 SQL（只信任 text block；thinking 内容是推理草稿，不能当 SQL 执行）
    raw_output = "".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    ).strip()
    if not raw_output:
        raise RuntimeError(
            "LLM 返回了空的响应内容，请检查模型配置。"
            f"响应类型: {[getattr(b, 'type', 'unknown') for b in response.content]}"
        )

    sql = _extract_sql_from_response(raw_output)

    # 5. 处理 UNANSWERABLE
    if sql.upper().strip().rstrip(".。") == "UNANSWERABLE":
        raise ValueError(
            "您的问题无法转换为 SQL 查询，请尝试更具体的数据分析问题。"
            "例如：'每种商品类别的销售额' 或 '每天的订单数趋势'。"
        )

    return sql
