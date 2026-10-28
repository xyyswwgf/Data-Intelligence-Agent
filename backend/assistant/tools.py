"""
推理 Agent 的工具集 — 把平台已有能力包装成大模型可调用的函数

每个工具返回两份结果：
  for_model  精简文本，进入模型上下文（控制 token：表格只给前若干行 + 统计）
  display    完整结构化数据，只给前端渲染（表格、图表、引用卡片），不占模型上下文
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.assistant import memory as memory_store
from backend.assistant.tokens import truncate_to_tokens
from backend.database import execute_readonly_sql, get_ddl, get_table_row_counts
from backend.llm_gateway import ToolSpec
from backend.sql_guard import strip_trailing_semicolon, validate_sql

_MODEL_TABLE_ROWS = 30
_TOOL_RESULT_TOKENS = 2500


@dataclass
class ToolContext:
    user_id: int
    session_id: str
    memory_enabled: bool
    target_table: str | None = None
    role: str = "viewer"
    activated_skills: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    for_model: str
    display: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    summary: str = ""


@dataclass
class Tool:
    spec: ToolSpec
    handler: Callable[[dict, ToolContext], ToolResult]


def markdown_table(columns: list[str], rows: list[list], limit: int = _MODEL_TABLE_ROWS) -> str:
    if not columns:
        return "（无结果）"
    head = "| " + " | ".join(map(str, columns)) + " |\n| " + " | ".join("---" for _ in columns) + " |"
    body = "\n".join("| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows[:limit])
    more = f"\n…共 {len(rows)} 行，仅展示前 {limit} 行" if len(rows) > limit else ""
    return f"{head}\n{body}{more}"


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------


def _search_knowledge(args: dict, ctx: ToolContext) -> ToolResult:
    from backend.kb.retrieval import search

    query = str(args.get("query", "")).strip()
    result = search(query, top_k=min(int(args.get("top_k", 5) or 5), 8))
    hits = result["hits"]
    if not hits:
        return ToolResult("知识库中没有找到相关内容。", {"citations": []}, summary="未找到相关知识")
    lines = []
    citations = []
    for i, hit in enumerate(hits, start=1):
        where = hit.title + (f" > {hit.heading_path.split(' > ')[-1]}" if hit.heading_path else "")
        lines.append(f"[来源{i}] 《{where}》(页面: {hit.slug})\n{truncate_to_tokens(hit.content, 400)}")
        citations.append(hit.to_dict() | {"n": i})
    return ToolResult("\n\n".join(lines), {"citations": citations, "retrieval": result["mode"]},
                      summary=f"检索到 {len(hits)} 条知识（{result['mode']}）")


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------


def _list_tables(args: dict, ctx: ToolContext) -> ToolResult:
    counts = get_table_row_counts()
    comments: dict[str, str] = {}
    current: str | None = None
    for line in get_ddl().splitlines():
        if line.startswith("CREATE TABLE"):
            current = line.split('"')[1]
        elif line.startswith(");") and "--" in line and current:
            comments[current] = line.split("--", 1)[1].strip()
    lines = [f"- {name}（{rows} 行）{'：' + comments[name] if name in comments else ''}" for name, rows in sorted(counts.items())]
    return ToolResult("\n".join(lines) or "没有业务表", {"tables": counts}, summary=f"{len(counts)} 张业务表")


def _describe_tables(args: dict, ctx: ToolContext) -> ToolResult:
    wanted = {str(t).strip().strip('"') for t in (args.get("tables") or []) if str(t).strip()}
    ddl = get_ddl()
    blocks = [b.strip() for b in ddl.split("\n\n") if b.strip()]
    picked = [b for b in blocks if not wanted or any(f'"{t}"' in b.splitlines()[0] for t in wanted)]
    if not picked:
        return ToolResult(f"没有找到表：{', '.join(wanted)}。请先调用 list_tables 查看可用的表。", ok=False,
                          summary="表不存在")
    return ToolResult(truncate_to_tokens("\n\n".join(picked), _TOOL_RESULT_TOKENS), summary=f"读取 {len(picked)} 张表结构")


def _run_sql(args: dict, ctx: ToolContext) -> ToolResult:
    sql = str(args.get("sql", "")).strip()
    is_safe, reason = validate_sql(sql)
    if not is_safe:
        return ToolResult(f"SQL 被安全网关拦截：{reason}。只允许单条 SELECT。", {"sql": sql}, ok=False, summary="被安全网关拦截")
    try:
        result = execute_readonly_sql(strip_trailing_semicolon(sql), max_rows=500)
    except Exception as exc:
        error = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0][:300]
        return ToolResult(f"SQL 执行失败：{error}。请检查表名/列名（可调用 describe_tables）后修正重试。",
                          {"sql": sql, "error": error}, ok=False, summary="执行失败")
    table = markdown_table(result["columns"], result["data"])
    return ToolResult(
        truncate_to_tokens(f"返回 {result['row_count']} 行{'（已截断）' if result['truncated'] else ''}：\n{table}", _TOOL_RESULT_TOKENS),
        {"sql": sql, "columns": result["columns"], "data": result["data"], "row_count": result["row_count"],
         "truncated": result["truncated"], "purpose": args.get("purpose", "")},
        summary=f"返回 {result['row_count']} 行",
    )


def _python_analysis(args: dict, ctx: ToolContext) -> ToolResult:
    from backend.assistant.python_sandbox import SandboxRejected, run_sandboxed
    from backend.config import PYTHON_SANDBOX_ENABLED, PYTHON_SANDBOX_MAX_ROWS

    if not PYTHON_SANDBOX_ENABLED:
        return ToolResult("管理员已关闭 Python 分析沙箱（PYTHON_SANDBOX_ENABLED=false）。", ok=False, summary="沙箱已关闭")
    sql = str(args.get("sql", "")).strip()
    code = str(args.get("code", ""))
    is_safe, reason = validate_sql(sql)
    if not is_safe:
        return ToolResult(f"取数 SQL 被安全网关拦截：{reason}", {"sql": sql, "code": code}, ok=False, summary="SQL 被拦截")
    try:
        data = execute_readonly_sql(strip_trailing_semicolon(sql), max_rows=PYTHON_SANDBOX_MAX_ROWS)
    except Exception as exc:
        error = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0][:300]
        return ToolResult(f"取数 SQL 执行失败：{error}", {"sql": sql, "code": code}, ok=False, summary="取数失败")
    # 失败时告诉模型 df 的真实结构：小模型常常忘记数据已经加载好，去重新读取或构造 DataFrame
    sample = "\n".join(" | ".join(str(v) for v in row) for row in data["data"][:3])
    df_hint = (f"\n\n数据说明：sql 的查询结果已经加载为 DataFrame，变量名就是 df（{data['row_count']} 行），"
               f"列为 {', '.join(data['columns'])}。前 3 行：\n{sample}\n"
               "直接对 df 计算即可，不要重新读取数据、不要定义其他数据变量，最后把结果赋值给 result。")
    try:
        run = run_sandboxed(code, data["columns"], data["data"])
    except SandboxRejected as exc:
        return ToolResult(f"{exc}。{df_hint}",
                          {"sql": sql, "code": code, "problems": exc.problems}, ok=False, summary="代码未通过安全校验")
    display = {"sql": sql, "code": code, "input_rows": data["row_count"], "input_truncated": data["truncated"],
               "stdout": run.stdout, "chart": run.chart, "elapsed_ms": run.elapsed_ms, "python": True}
    if not run.ok:
        return ToolResult(f"代码执行出错：{run.error}{df_hint}", display | {"error": run.error},
                          ok=False, summary="执行出错")
    parts = [f"输入数据 {data['row_count']} 行{'（已截断）' if data['truncated'] else ''}。"]
    if run.stdout.strip():
        parts.append(f"print 输出：\n{run.stdout.strip()[:1500]}")
    result = run.result
    if result and result["kind"] == "table":
        display |= {"columns": result["columns"], "data": result["data"], "row_count": result["row_count"],
                    "truncated": result["truncated"]}
        parts.append(f"result（{result['row_count']} 行）：\n{markdown_table(result['columns'], result['data'])}")
    elif result:
        display["value"] = result["value"]
        parts.append(f"result：{json.dumps(result['value'], ensure_ascii=False, default=str)[:2000]}")
    else:
        parts.append("代码没有给 result 赋值。")
    return ToolResult(truncate_to_tokens("\n\n".join(parts), _TOOL_RESULT_TOKENS), display,
                      summary=f"输入 {data['row_count']} 行 · {run.elapsed_ms}ms")


# ---------------------------------------------------------------------------
# 分析 Agent
# ---------------------------------------------------------------------------


def _analysis_digest(result: dict) -> str:
    report = result.get("report") or {}
    metrics = "；".join(
        f"{m['name']}={m['current']}" + (f"（环比 {m['change_rate']:+.2%}）" if m.get("change_rate") is not None else "")
        for m in (result.get("metrics") or [])[:8]
    )
    parts = [f"结论：{report.get('summary', '')}", f"指标：{metrics}"]
    if report.get("findings"):
        parts.append("发现：\n" + "\n".join(f"- {f}" for f in report["findings"]))
    if result.get("errors"):
        parts.append(f"错误：{result['errors']}")
    return truncate_to_tokens("\n".join(parts), _TOOL_RESULT_TOKENS)


def _business_analysis(args: dict, ctx: ToolContext) -> ToolResult:
    from backend.agent_service import run_analysis

    result = run_analysis(str(args.get("question", "")), user_id=ctx.user_id)
    return ToolResult(_analysis_digest(result), {"analysis": result}, ok=result["status"] == "completed",
                      summary=(result.get("report") or {}).get("summary", "")[:60] or result["status"])


def _analyze_table(args: dict, ctx: ToolContext) -> ToolResult:
    from backend.generic_table_agent import run_table_analysis

    table = str(args.get("table_name") or ctx.target_table or "")
    try:
        result = run_table_analysis(str(args.get("question", "")), table, user_id=ctx.user_id)
    except (PermissionError, ValueError) as exc:
        return ToolResult(f"无法分析：{exc}", ok=False, summary=str(exc))
    return ToolResult(_analysis_digest(result), {"analysis": result}, ok=result["status"] == "completed",
                      summary=(result.get("report") or {}).get("summary", "")[:60] or result["status"])


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------


def _remember(args: dict, ctx: ToolContext) -> ToolResult:
    if not ctx.memory_enabled:
        return ToolResult("用户已关闭记忆功能，未保存。", ok=False, summary="记忆已关闭")
    try:
        saved = memory_store.save_memory(ctx.user_id, str(args.get("content", "")),
                                         str(args.get("category", "fact")), "explicit", ctx.session_id)
    except ValueError as exc:
        return ToolResult(f"未保存：{exc}", ok=False, summary=str(exc))
    verb = "已更新" if saved["action"] == "updated" else "已记住"
    return ToolResult(f"{verb}：{saved['memory']['content']}", {"memory": saved["memory"]}, summary=verb)


def _use_skill(args: dict, ctx: ToolContext) -> ToolResult:
    from backend.assistant.skills import get_skill, render_instructions

    name = str(args.get("name", "")).strip()
    skill = get_skill(name)
    if not skill:
        return ToolResult(f"没有名为 {name} 的技能，请从「可用技能」列表中选择。", ok=False, summary="技能不存在")
    ctx.activated_skills.append(name)
    return ToolResult(render_instructions(skill), {"skill": {"name": skill["name"], "title": skill["title"]}},
                      summary=f"已加载「{skill['title']}」")


BUILTIN_TOOLS: dict[str, Tool] = {
    tool.spec.name: tool
    for tool in [
        Tool(ToolSpec("search_knowledge", "检索企业知识库（制度、指标口径、业务规则、手册等）。回答涉及定义、口径、流程、规定时先调用。",
                      {"type": "object", "properties": {
                          "query": {"type": "string", "description": "检索语句，用业务关键词描述"},
                          "top_k": {"type": "integer", "description": "返回条数，默认 5"}},
                       "required": ["query"]}), _search_knowledge),
        Tool(ToolSpec("list_tables", "列出可查询的业务数据表、行数和说明。不知道数据在哪张表时先调用。",
                      {"type": "object", "properties": {}}), _list_tables),
        Tool(ToolSpec("describe_tables", "查看指定数据表的字段定义（列名、类型、注释），写 SQL 前调用以避免写错列名。",
                      {"type": "object", "properties": {
                          "tables": {"type": "array", "items": {"type": "string"}, "description": "表名列表"}},
                       "required": ["tables"]}), _describe_tables),
        Tool(ToolSpec("run_sql", "在只读环境执行一条 PostgreSQL SELECT 查询并返回结果。表名列名用双引号包裹；明细查询加 LIMIT。",
                      {"type": "object", "properties": {
                          "sql": {"type": "string", "description": "单条 SELECT 语句"},
                          "purpose": {"type": "string", "description": "这条查询要回答什么"}},
                       "required": ["sql"]}), _run_sql),
        Tool(ToolSpec("business_analysis", "门店经营分析：各门店销售额、毛利、手机台数、毛利目标达成，产品线 / 店员 / 收款方式结构，"
                      "长库龄库存。回答「门店经营怎么样」「为什么没达成」「哪个门店 / 产品线 / 店员拖累」这类问题时使用；"
                      "question 中可带门店名（望京 / 南锣 / 乐堤港 / 阳光）和月份。",
                      {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}),
             _business_analysis),
        Tool(ToolSpec("analyze_imported_table", "对用户导入的 CSV 表（csv_ 前缀）做趋势、分组、缺失、异常分析。",
                      {"type": "object", "properties": {
                          "table_name": {"type": "string", "description": "csv_ 开头的表名"},
                          "question": {"type": "string"}},
                       "required": ["question"]}), _analyze_table),
        Tool(ToolSpec("remember", "把关于用户的长期有用信息存入记忆（偏好、负责的业务、目标、要求以后遵守的做法）。"
                      "用户明确要求记住时必须调用；不要存一次性的查询结果。",
                      {"type": "object", "properties": {
                          "content": {"type": "string", "description": "以第三人称描述，如：用户负责华北区域销售"},
                          "category": {"type": "string", "enum": list(memory_store.CATEGORIES)}},
                       "required": ["content"]}), _remember),
        Tool(ToolSpec("python_analysis",
                      "用 Python（pandas / numpy）对数据做 SQL 不方便的计算：透视、同比环比、占比、排名变化、"
                      "相关性、分布统计、异常检测、多步数据清洗等。sql 的查询结果会自动加载为 DataFrame，变量名固定为 df，"
                      "code 里直接使用 df（不要自己读取或构造数据），把最终结果赋值给 result（DataFrame / Series / 数值 / dict）；"
                      "可选给 chart 赋值 {\"type\": \"bar|line|pie\", \"x\": 列名, \"y\": [列名]} 生成图表。"
                      "只能 import pandas、numpy、math、statistics、datetime、re、collections、itertools、functools、json，"
                      "不能读写文件、联网，也不能使用下划线属性和 str.format（用 f-string）。",
                      {"type": "object", "properties": {
                          "sql": {"type": "string", "description": "取数用的单条 SELECT"},
                          "code": {"type": "string", "description": "pandas 代码，数据在变量 df 中，结果赋值给 result"},
                          "purpose": {"type": "string", "description": "这段分析要回答什么"}},
                       "required": ["sql", "code"]}), _python_analysis),
        Tool(ToolSpec("use_skill", "加载一个技能的详细指引。用户请求符合「可用技能」中某项的适用场景时，先调用它再开始工作。",
                      {"type": "object", "properties": {"name": {"type": "string", "description": "技能标识"}},
                       "required": ["name"]}), _use_skill),
    ]
}


_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


@dataclass
class Registry:
    """本轮可用的工具：内置工具 + 管理员添加的 HTTP / SQL 工具，按启用状态和角色过滤。"""

    specs: list[ToolSpec]
    custom: dict[str, dict]


def load_registry(role: str, memory_enabled: bool, has_skills: bool) -> Registry:
    from backend.database import get_connection
    from sqlalchemy import text

    with get_connection() as conn:
        rows = conn.execute(text("SELECT * FROM agent_tools WHERE enabled ORDER BY id")).mappings().all()
    specs, custom = [], {}
    for row in rows:
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(row["min_role"], 0):
            continue
        name = row["name"]
        if row["kind"] == "builtin":
            if name not in BUILTIN_TOOLS or (name == "remember" and not memory_enabled) or (name == "use_skill" and not has_skills):
                continue
            specs.append(BUILTIN_TOOLS[name].spec)
        else:
            specs.append(ToolSpec(name, row["description"], row["parameters"]))
            custom[name] = dict(row)
    return Registry(specs, custom)


def tools_schema(specs: list[ToolSpec]) -> list[dict]:
    return [{"name": s.name, "description": s.description, "parameters": s.parameters} for s in specs]


def execute(name: str, args: dict, ctx: ToolContext, registry: Registry | None = None) -> ToolResult:
    """执行工具；只允许调用本轮注册表里的工具（已停用或无权限的工具即使模型编出名字也不会执行）。"""
    allowed = {s.name for s in registry.specs} if registry else set(BUILTIN_TOOLS)
    if name not in allowed:
        return ToolResult(f"工具 {name} 不可用。可用工具：{', '.join(sorted(allowed))}", ok=False, summary="工具不可用")
    if "_raw" in args:
        return ToolResult(f"工具参数不是合法 JSON：{args['_raw'][:200]}", ok=False, summary="参数格式错误")
    try:
        if registry and name in registry.custom:
            return _run_custom(registry.custom[name], args)
        return BUILTIN_TOOLS[name].handler(args, ctx)
    except Exception as exc:  # 工具异常转成观察结果，让模型有机会自我修正
        return ToolResult(f"工具执行失败：{exc}", ok=False, summary=str(exc)[:60] or "执行异常")


def _run_custom(tool: dict, args: dict) -> ToolResult:
    from backend.assistant.custom_tools import run_http_tool, run_sql_tool

    if tool["kind"] == "http":
        text_out, display = run_http_tool(tool, args)
        return ToolResult(text_out, {"http": display}, summary=f"HTTP {display['status']}")
    text_out, display = run_sql_tool(tool, args)
    return ToolResult(text_out, display, summary=f"返回 {display['row_count']} 行")


def args_preview(args: dict) -> str:
    return json.dumps(args, ensure_ascii=False)[:500]
