"""
行业包：把行业相关的内容从平台核心中拿出来

平台核心（智能助手、SQL 查询、知识库、导入表分析）不包含任何行业假设；
行业包提供该行业的：指标口径（SQL 生成规则）、助手提示、专属分析流程（工具 + 模式）、
首页推荐问题、输入框提示、专属数据导入入口。

同一时间启用一个行业包（存放在 system_settings.industry_pack，管理员在「设置 → 行业包」中切换）。
新增行业时在 PACKS 中注册一个 IndustryPack 即可，前端不用改。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import text

logger = logging.getLogger("nl2sql.packs")

DEFAULT_PACK = "retail_store"


@dataclass(frozen=True)
class IndustryPack:
    code: str
    name: str
    description: str
    # 专属分析流程（输入框中的第二个模式）；None 表示只能分析导入的数据表
    analysis_label: str | None = None
    analysis_tool: str | None = None  # 提供给智能助手的内置工具名
    # 追加到 SQL 生成提示中的口径规则、示例
    sql_rules: str = ""
    sql_examples: str = ""
    # 追加到智能助手系统提示中的行业说明
    assistant_hint: str = ""
    # 未配置大模型时，命中这些词就走专属分析流程
    analysis_words: tuple[str, ...] = ()
    # 首页推荐问题（按模式）和输入框提示
    suggestions: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    placeholders: dict[str, str] = field(default_factory=dict)
    # 知识库中是否显示「导入门店数据」入口
    store_import: bool = False
    # 数据时间范围提示（SQL 生成与助手用来理解「本月」）
    coverage: Callable[[], str] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "description": self.description,
            "analysis_label": self.analysis_label, "suggestions": self.suggestions,
            "placeholders": self.placeholders, "store_import": self.store_import,
        }


def _store_coverage() -> str:
    try:
        from backend.database import execute_sql

        row = execute_sql("SELECT MIN(sale_date)::text, MAX(sale_date)::text, MAX(month_label) FROM fact_store_sales")["data"][0]
    except Exception:
        row = None
    if not row or not row[0]:
        return "数据的时间范围以表中实际数据为准。"
    return (f"门店销售数据覆盖 {row[0]} 到 {row[1]}。用户说「本月」时默认指 {row[2]}（数据中最新的月份），"
            "「上月」指它的前一个月。")


RETAIL_STORE = IndustryPack(
    code="retail_store",
    name="门店零售",
    description="连锁门店的销售、毛利、目标达成、店员业绩、库龄与收款分析；支持导入收银台账、收款明细、ERP 与门店指导书。",
    analysis_label="经营分析",
    analysis_tool="business_analysis",
    sql_rules=("门店口径：销售额 = SUM(sale_price)，毛利 = SUM(gross_profit)，手机台数 = COUNT(*) FILTER (WHERE is_phone)；"
               "按门店统计时 JOIN \"dim_store\" 取 short_name。"),
    sql_examples="""### 行业示例（门店零售）
- 「每个门店的销售额」→ SELECT d."short_name" AS "门店", SUM(s."sale_price") AS "销售额" FROM "fact_store_sales" s JOIN "dim_store" d ON s."store_id" = d."store_id" GROUP BY d."short_name"
- 「每天卖了多少台手机」→ SELECT "sale_date", COUNT(*) FILTER (WHERE "is_phone") AS "手机台数" FROM "fact_store_sales" GROUP BY "sale_date"
- 「店员毛利排名」→ SELECT "salesperson", SUM("gross_profit") AS "毛利", RANK() OVER (ORDER BY SUM("gross_profit") DESC) AS "排名" FROM "fact_store_sales" GROUP BY "salesperson"
- 「按月统计销售额」→ SELECT "month_label" AS "月份", SUM("sale_price") AS "销售额" FROM "fact_store_sales" GROUP BY "month_label"
""",
    assistant_hint=("当前行业：门店零售。门店经营概况、任务达成、「为什么没达成 / 哪些拖累」这类问题优先用 business_analysis；"
                    "产品政策、提成、考核、运营商套餐等规则先 search_knowledge。"),
    analysis_words=("为什么", "原因", "下降", "上升", "归因", "拖累", "经营", "销售额", "毛利", "达成", "目标", "台数", "库龄", "门店"),
    suggestions={
        "assistant": [
            {"icon": "trend", "title": "本月各门店毛利和目标达成情况怎么样？", "desc": "自己找表、写 SQL、核对口径"},
            {"icon": "sparkles", "title": "达成率最低的门店为什么没达成？给出三条建议", "desc": "调用门店分析 + 结合门店指导书"},
            {"icon": "book", "title": "店长店提的档位和点位是怎么规定的？", "desc": "知识库考核规则"},
            {"icon": "bulb", "title": "记住：汇报金额用万元，保留两位小数", "desc": "写入长期记忆，之后自动参考"},
        ],
        "agent": [
            {"icon": "trend", "title": "本月各门店经营怎么样？", "desc": "销售额 / 毛利 / 手机台数 / 目标达成"},
            {"icon": "target", "title": "哪些门店和产品线没完成毛利目标？", "desc": "定位差距最大的考核项"},
            {"icon": "coins", "title": "店员的业绩和提成排名", "desc": "店员毛利、个人目标达成"},
            {"icon": "alert", "title": "哪些门店长库龄库存最多？", "desc": "库龄超过 90 天的商品"},
        ],
        "kb": [
            {"icon": "book", "title": "店长店提的档位和点位是怎么规定的？", "desc": "店长店员考核"},
            {"icon": "coins", "title": "本月主推机型的提成是多少？", "desc": "产品政策"},
            {"icon": "alert", "title": "清尾机怎么计算分值？", "desc": "清尾明细"},
            {"icon": "map", "title": "运营商套餐的红包和毛利怎么算？", "desc": "运营商政策"},
        ],
        "query": [
            {"icon": "code", "title": "每个门店本月的销售额和毛利", "desc": "分组聚合"},
            {"icon": "trend", "title": "每天卖了多少台手机", "desc": "时间序列"},
            {"icon": "map", "title": "毛利最高的 5 个店员", "desc": "排序 + LIMIT"},
            {"icon": "coins", "title": "各门店库龄超过 90 天的库存金额", "desc": "多表关联"},
        ],
    },
    placeholders={
        "agent": "描述你的经营问题，例如：本月各门店毛利目标达成怎么样？",
        "kb": "向企业知识库提问，例如：本月主推机型的提成是多少？",
        "query": "用自然语言查数据，例如：每个门店本月的销售额和毛利",
    },
    store_import=True,
    coverage=_store_coverage,
)

GENERIC = IndustryPack(
    code="generic",
    name="通用数据分析",
    description="不限行业：导入任意 CSV / Excel 表，自动做趋势、分组、缺失与异常分析；知识库存放制度、口径与文档。",
    sql_rules="指标口径以表注释和知识库为准；口径不明确时在结果列名中写清楚计算方式。",
    assistant_hint=("当前为通用数据分析：先用 list_tables / describe_tables 了解有哪些数据；"
                    "用户导入的表用 analyze_imported_table 做整体分析；制度、口径类问题先 search_knowledge。"),
    analysis_words=("为什么", "原因", "下降", "上升", "趋势", "异常"),
    suggestions={
        "assistant": [
            {"icon": "table", "title": "现在有哪些数据可以分析？", "desc": "列出数据表和字段"},
            {"icon": "trend", "title": "最近三个月的关键指标有什么变化？", "desc": "自己找表、写 SQL、做对比"},
            {"icon": "book", "title": "我们的报销制度是怎么规定的？", "desc": "检索知识库并给出处"},
            {"icon": "bulb", "title": "记住：汇报金额用万元，保留两位小数", "desc": "写入长期记忆，之后自动参考"},
        ],
        "kb": [
            {"icon": "book", "title": "公司的考勤和请假制度是什么？", "desc": "制度文档"},
            {"icon": "coins", "title": "报销需要哪些材料，多久到账？", "desc": "财务流程"},
            {"icon": "target", "title": "这季度的重点目标有哪些？", "desc": "经营计划"},
            {"icon": "map", "title": "新员工入职要完成哪些步骤？", "desc": "入职指南"},
        ],
        "query": [
            {"icon": "table", "title": "每个表有多少行数据？", "desc": "数据概览"},
            {"icon": "trend", "title": "按月统计金额合计", "desc": "时间序列"},
            {"icon": "map", "title": "金额最高的 10 条记录", "desc": "排序 + LIMIT"},
            {"icon": "chart", "title": "按类别统计数量和占比", "desc": "分组聚合"},
        ],
    },
    placeholders={
        "kb": "向企业知识库提问，例如：报销需要哪些材料？",
        "query": "用自然语言查数据，例如：按月统计金额合计",
    },
)

PACKS: dict[str, IndustryPack] = {p.code: p for p in (RETAIL_STORE, GENERIC)}
# 属于某个行业包的内置工具：只有该行业包启用时才提供给智能助手、出现在「发现」中
PACK_TOOLS = {p.analysis_tool for p in PACKS.values() if p.analysis_tool}


def active_pack() -> IndustryPack:
    """当前启用的行业包；读取失败（如迁移前）时用默认包。"""
    try:
        from backend.database import get_connection

        with get_connection() as conn:
            code = conn.execute(text("SELECT value #>> '{}' FROM system_settings WHERE key = 'industry_pack'")).scalar()
    except Exception:
        code = None
    return PACKS.get(code or DEFAULT_PACK, PACKS[DEFAULT_PACK])


def set_active_pack(code: str) -> IndustryPack:
    if code not in PACKS:
        raise ValueError(f"未知的行业包：{code}")
    from backend.database import get_connection

    with get_connection() as conn:
        conn.execute(text("""
            INSERT INTO system_settings (key, value) VALUES ('industry_pack', to_jsonb(CAST(:v AS text)))
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """), {"v": code})
    return PACKS[code]


def coverage_hint(pack: IndustryPack | None = None) -> str:
    pack = pack or active_pack()
    if pack.coverage:
        try:
            return pack.coverage()
        except Exception:
            logger.exception("读取数据时间范围失败")
    return "业务数据不一定更新到今天，时间范围以表中实际数据为准。"
