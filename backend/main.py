"""
=============================================================================
NL2SQL 智能查询系统 — FastAPI 入口
=============================================================================

核心链路:
  用户自然语言 → POST /api/query
    → llm_service.generate_sql()    # 大模型生成 SQL
    → sql_guard.validate_sql()       # 安全校验（防注入）
    → database.execute_sql()         # 执行查询
    → 返回 {sql, columns, data, row_count}

安全策略:
  - 纵深防御：LLM Prompt 约束 + 后端 SQL 校验 + 数据库只读事务，三重保障
  - 只允许 SELECT 查询，拒绝任何 DDL/DML/DCL 操作
  - 分号堆叠注入检测，查询超时与返回行数上限

并发模型:
  数据库与 LLM 调用都是同步阻塞的，因此路由用普通 def 定义，
  由 FastAPI 放进线程池执行，避免一个慢查询/慢 LLM 调用卡住整个事件循环。

启动方式:
  DB_PASSWORD=xxx uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
=============================================================================
"""

import time
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.nl2sql import QueryBlocked, QueryFailed, QueryLLMError, QueryUnanswerable, answer_query
from backend.database import (
    execute_sql,
    get_table_names,
    get_table_row_counts,
    quote_identifier,
    table_exists,
    drop_table,
)
from backend.excel_importer import is_excel, read_excel
from backend.csv_importer import (
    read_csv,
    read_rows,
    sanitize_column_name,
    infer_column_types,
    profile_import_file,
    import_table,
    derive_table_name,
    normalize_table_name,
    auto_detect_trailing_rows,
    RESERVED_COLUMN_NAMES,
)
from backend.agent_service import run_analysis, list_history, get_history_detail, export_report
from backend import auth
from backend.auth import CurrentUser, get_current_user, require_role
from backend.migrations import run_migrations
from backend.routers import agent_admin_routes, auth_routes, chat_routes, kb_routes, store_data_routes
from backend.generic_table_agent import run_table_analysis
from backend.config import (
    CORS_ORIGINS,
    CSV_BATCH_SIZE,
    CSV_MAX_FILE_SIZE_MB,
    CSV_MAX_ROWS,
    DB_HOST,
    DB_NAME,
    DB_PORT,
)

APP_VERSION = "2.0.0"

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nl2sql")

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时：执行数据库迁移 → 初始化管理员 → 补齐未完成的知识库索引。"""
    applied = run_migrations()
    if applied:
        logger.info("数据库迁移完成: %s", applied)
    auth.bootstrap_admin()
    from backend.kb.indexer import reindex

    queued = reindex()
    if queued:
        logger.info("已提交 %d 个待索引的知识库页面", queued)
    yield


app = FastAPI(
    title="DataMind 数据智能 Agent",
    description="自然语言数据分析 + 企业知识库（RAG / Wiki）| 私有化部署",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS 中间件 — 允许前端跨域访问
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(kb_routes.router)
app.include_router(store_data_routes.router)
app.include_router(chat_routes.router)
app.include_router(agent_admin_routes.router)

# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """自然语言查询请求"""

    question: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="用户的自然语言问题，例如：'每种商品类别的总销售额？'",
        examples=["每种商品类别的总销售额是多少？"],
    )


class QueryResponse(BaseModel):
    """查询结果响应"""

    sql: str = Field(..., description="大模型生成并已执行的 SQL 语句")
    columns: list[str] = Field(..., description="结果列名列表")
    data: list[list] = Field(..., description="二维数据数组，每行是一个列表")
    row_count: int = Field(..., description="结果行数")
    truncated: bool = Field(False, description="结果是否因超过行数上限被截断")
    attempts: int = Field(1, description="生成 SQL 的尝试次数（>1 表示经过了自我修正）")


class ImportResponse(BaseModel):
    """CSV 导入结果响应"""

    table_name: str = Field(..., description="创建的数据库表名")
    original_filename: str = Field(..., description="上传的原始文件名")
    columns: list[dict] = Field(..., description="列信息列表 [{name, type, nullable}]")
    row_count: int = Field(..., description="导入的数据行数")
    message: str = Field(..., description="导入结果描述")


class TableInfo(BaseModel):
    """表信息"""

    table_name: str
    row_count: int
    imported: bool
    imported_at: str | None = None


class TableListResponse(BaseModel):
    """表列表响应"""

    tables: list[TableInfo]


class DeleteResponse(BaseModel):
    """删除表响应"""

    deleted: str = Field(..., description="被删除的表名")
    message: str = Field(..., description="操作结果描述")


class AnalyzeRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    analysis_mode: Literal["auto"] = Field("auto", description="分析模式，当前仅支持 auto")


class TableAnalyzeRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    table_name: str = Field(..., min_length=1, max_length=128)
    analysis_mode: Literal["auto"] = Field("auto", description="分析模式，当前仅支持 auto")


def _load_table_details(imported_only: bool = False) -> list[dict]:
    """表名、行数、是否导入、导入时间。行数一次 UNION 查询取回，避免逐表 N+1。"""
    counts = get_table_row_counts()
    table_details = []
    for table_name in get_table_names():
        imported = table_name.startswith("csv_")
        if imported_only and not imported:
            continue
        imported_at = None
        if imported:
            try:
                time_result = execute_sql(
                    f'SELECT MIN("_imported_at") AS t FROM {quote_identifier(table_name)}'
                )
                raw = time_result["data"][0][0] if time_result["data"] else None
                if raw:
                    imported_at = str(raw)
            except Exception:
                pass  # 旧版本导入的表可能没有 _imported_at 列

        table_details.append({
            "table_name": table_name,
            "row_count": counts.get(table_name, 0),
            "imported": imported,
            "imported_at": imported_at,
        })
    return table_details


def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or "unknown.csv"
    if not (filename.lower().endswith(".csv") or is_excel(filename)):
        raise HTTPException(status_code=400, detail="仅支持 .csv 和 .xlsx 格式的文件")
    max_bytes = CSV_MAX_FILE_SIZE_MB * 1024 * 1024
    try:
        # 多读 1 字节即可判断是否超限，避免把超大文件整个读进内存
        file_bytes = file.file.read(max_bytes + 1)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取文件失败: {str(exc)}")
    if len(file_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件大小超过限制（最大 {CSV_MAX_FILE_SIZE_MB}MB）")
    return filename, file_bytes


def _table_source_name(filename: str, excel_info: dict) -> str:
    """多工作表的 Excel 用「文件名_工作表名」推导默认表名，避免不同工作表导入时重名。"""
    if len(excel_info.get("sheets", [])) > 1:
        return f"{filename.rsplit('.', 1)[0]}_{excel_info['selected_sheet']}.xlsx"
    return filename


def _parse_upload(filename: str, file_bytes: bytes, sheet_name: str | None, **options):
    """CSV 与 Excel 统一解析为 ParsedCsv；Excel 额外返回工作表列表。"""
    if is_excel(filename):
        raw_rows, sheets, selected = read_excel(file_bytes, sheet_name)
        parsed = read_rows(raw_rows, "xlsx", "sheet", **options)
        return parsed, {"sheets": sheets, "selected_sheet": selected}
    return read_csv(file_bytes, **options), {}


# ---------------------------------------------------------------------------
# 中间件：请求日志
# ---------------------------------------------------------------------------


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录每个请求的方法、路径和耗时"""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s → %d (%.0fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health_check():
    """
    健康检查接口

    可用于 Docker healthcheck、负载均衡探测、监控告警
    """
    try:
        execute_sql("SELECT 1")
        database = "ok"
    except Exception as exc:
        logger.warning("健康检查：数据库不可用 — %s", exc)
        database = "unavailable"
    return {
        "status": "healthy" if database == "ok" else "degraded",
        "service": "NL2SQL",
        "version": APP_VERSION,
        "database": database,
    }


@app.get("/api/info")
def data_source_info(user: CurrentUser = Depends(get_current_user)):
    """
    数据来源信息 — 告诉前端数据库里有什么、数据从哪来
    """
    return {
        "database": {
            "engine": "PostgreSQL 16",
            "host": f"{DB_HOST}:{DB_PORT}",
            "name": DB_NAME,
            "charset": "UTF8",
        },
        "tables": _load_table_details(),
        "pipeline": [
            {"step": 1, "actor": "用户", "action": "输入自然语言问题"},
            {"step": 2, "actor": "LLM 大模型", "action": "结合数据库 DDL 结构生成 SQL"},
            {"step": 3, "actor": "安全网关", "action": "校验 SQL，拦截危险操作"},
            {"step": 4, "actor": "PostgreSQL", "action": "执行 SELECT 查询"},
            {"step": 5, "actor": "前端", "action": "表格展示 + ECharts 智能图表"},
        ],
    }


@app.post("/api/query", response_model=QueryResponse)
def natural_language_query(req: QueryRequest, user: CurrentUser = Depends(get_current_user)):
    """
    核心接口：自然语言 → 数据查询

    ---
    ## 处理流程

    ```
    用户自然语言
      │
      ▼
    ┌─────────────────────────────┐
    │ Step 1: LLM 生成 SQL        │  ← llm_service.generate_sql()
    │   - 提取最新数据库 DDL       │
    │   - 拼接 Prompt 发送给大模型  │
    │   - 从响应中提取纯 SQL       │
    └──────────────┬──────────────┘
                   ▼
    ┌─────────────────────────────┐
    │ Step 2: SQL 安全校验        │  ← sql_guard.validate_sql()
    │   - 必须以 SELECT 开头      │
    │   - 禁止 DROP/DELETE/UPDATE │
    │   - 检测分号堆叠注入        │
    └──────────────┬──────────────┘
                   ▼
    ┌─────────────────────────────┐
    │ Step 3: 执行查询            │  ← database.execute_readonly_sql()
    │   - READ ONLY 事务 + 超时   │
    │   - 结果行数上限            │
    │   - 失败则带报错让 LLM 修正 │
    └──────────────┬──────────────┘
                   ▼
            {sql, columns, data, row_count}
    ```

    ## 错误码

    | HTTP 状态码 | 含义 |
    |------------|------|
    | 200 | 查询成功 |
    | 400 | 问题无效或无法转为 SQL |
    | 403 | SQL 安全校验未通过（疑似注入） |
    | 500 | 数据库执行失败（已尝试让 LLM 根据报错自我修正） |
    | 502 | LLM API 调用失败 |
    """
    try:
        result = answer_query(req.question.strip())
    except QueryUnanswerable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except QueryLLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except QueryBlocked as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except QueryFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return QueryResponse(**result)


@app.post("/api/analyze")
def analyze_business_question(req: AnalyzeRequest, user: CurrentUser = Depends(get_current_user)):
    """
    数据智能 Agent 分析接口。

    该接口面向经营分析问题，会自动执行规划、知识检索、多条 SQL 查询、
    指标计算、归因分析、图表生成和报告输出。
    """
    question = req.question.strip()
    logger.info("收到 Agent 分析请求: %s", question)
    try:
        return run_analysis(question, req.analysis_mode, user.id)
    except Exception as exc:
        logger.error("Agent 分析失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"Agent 分析失败: {str(exc)}")


@app.post("/api/analyze/table")
def analyze_imported_table(req: TableAnalyzeRequest, user: CurrentUser = Depends(get_current_user)):
    """
    CSV 导入表通用分析接口。

    该接口只允许分析 csv_ 前缀的导入表，会自动识别字段角色并生成只读 SQL、
    图表和结构化报告。
    """
    question = req.question.strip()
    table_name = req.table_name.strip()
    logger.info("收到 CSV 表 Agent 分析请求: %s / %s", table_name, question)
    try:
        return run_table_analysis(question, table_name, req.analysis_mode, user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("CSV 表 Agent 分析失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"CSV 表 Agent 分析失败: {str(exc)}")


@app.get("/api/analysis/history")
def analysis_history(user: CurrentUser = Depends(get_current_user)):
    # 管理员可以看到所有人的分析，其他成员只看到自己的
    return {"items": list_history(user_id=None if user.has_role("admin") else user.id)}


@app.get("/api/analysis/{run_id}")
def analysis_detail(run_id: str, user: CurrentUser = Depends(get_current_user)):
    detail = get_history_detail(run_id, None if user.has_role("admin") else user.id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"分析记录 {run_id} 不存在")
    return detail


@app.get("/api/analysis/{run_id}/export")
def analysis_export(
    run_id: str,
    format: str = Query("markdown", pattern="^(markdown|html)$"),
    user: CurrentUser = Depends(get_current_user),
):
    exported = export_report(run_id, format, None if user.has_role("admin") else user.id)
    if not exported:
        raise HTTPException(status_code=404, detail=f"分析记录 {run_id} 不存在")
    content, media_type, filename = exported
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# ---------------------------------------------------------------------------
# CSV 导入相关路由
# ---------------------------------------------------------------------------


@app.post("/api/import/csv/preview")
def preview_csv(
    file: UploadFile = File(..., description="CSV 文件"),
    header_row: int = Form(0, description="表头所在行号（0=自动检测）"),
    data_start_row: int = Form(0, description="数据起始行号（0=自动）"),
    skip_trailing: int = Form(-1, description="跳过末尾行数（-1=自动检测）"),
    combine_headers: int = Form(1, ge=1, le=10, description="合并表头行数"),
    sheet_name: str | None = Form(None, description="Excel 工作表名（默认第一个）"),
    user: CurrentUser = Depends(require_role("editor")),
):
    """预览 CSV / Excel 文件（不创建表），自动检测编码、表头行和末尾非数据行"""
    filename, file_bytes = _read_upload(file)

    # ── 解析 ──
    try:
        parsed, excel_info = _parse_upload(
            filename, file_bytes, sheet_name,
            has_header=True,
            header_row=max(header_row, 0),
            data_start_row=data_start_row if data_start_row > 0 else None,
            skip_trailing_rows=max(skip_trailing, 0),
            combine_header_rows=combine_headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    headers, rows = parsed.headers, parsed.rows

    # ── 自动检测末尾非数据行 ──
    auto_skip = 0
    if skip_trailing < 0:
        auto_skip = auto_detect_trailing_rows(rows)
        if auto_skip > 0:
            rows = rows[:len(rows) - auto_skip]
            logger.info("自动跳过末尾 %d 行非数据内容", auto_skip)

    if len(rows) > CSV_MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"CSV 行数 ({len(rows)}) 超过限制 ({CSV_MAX_ROWS})")

    # ── 列名清理 + 类型检测 ──
    used_names: set[str] = set()
    clean_headers = [sanitize_column_name(h, used_names) for h in headers]
    column_types = infer_column_types(clean_headers, rows)
    file_profile = profile_import_file(
        _table_source_name(filename, excel_info),
        headers,
        clean_headers,
        rows,
        column_types,
        parsed.encoding,
        delimiter=parsed.delimiter,
    )

    # ── 每列的样本值 ──
    preview_count = min(20, len(rows))
    columns_info = []
    for i, col in enumerate(column_types):
        columns_info.append({
            "name": col["name"],
            "original_name": headers[i] if i < len(headers) else col["name"],
            "detected_type": col["type"],
            "sample_values": [rows[j][i] for j in range(min(5, len(rows)))],
        })

    return {
        **excel_info,
        "encoding": parsed.encoding,
        "delimiter": parsed.delimiter,
        "total_rows": len(rows),
        "total_cols": len(clean_headers),
        "total_valid_lines": parsed.total_lines,
        # 返回实际使用的行号（自动检测后的值），前端确认导入时原样回传
        "header_row": parsed.header_row,
        "data_start_row": parsed.data_start_row,
        "combine_header_rows": combine_headers,
        "auto_skip_trailing": auto_skip,
        "skip_trailing_rows": skip_trailing if skip_trailing >= 0 else auto_skip,
        "original_headers": headers,
        "clean_headers": clean_headers,
        "columns": columns_info,
        "preview_rows": [list(row) for row in rows[:preview_count]],
        "file_profile": file_profile,
    }


@app.post("/api/import/csv", response_model=ImportResponse, status_code=201)
def import_csv(
    file: UploadFile = File(..., description="CSV 文件"),
    overwrite: bool = Form(False, description="是否覆盖同名表"),
    table_name: str | None = Form(None, description="自定义表名（可选）"),
    has_header: bool = Form(True, description="第一行是否为表头"),
    header_row: int = Form(1, description="表头所在行号（0=自动检测）"),
    data_start_row: int = Form(0, description="数据起始行号（0=自动）"),
    skip_trailing: int = Form(0, ge=0, description="跳过末尾行数"),
    combine_headers: int = Form(1, ge=1, le=10, description="合并表头行数"),
    column_names: str | None = Form(None, description="自定义列名 JSON数组"),
    column_types_override: str | None = Form(None, description="自定义列类型 JSON对象（可选）"),
    primary_key_column: str | None = Form(None, description="指定主键列名（可选，不指定则自动生成_id）"),
    sheet_name: str | None = Form(None, description="Excel 工作表名（默认第一个）"),
    user: CurrentUser = Depends(require_role("editor")),
):
    """
    上传 CSV 文件并自动创建数据库表（支持预览后的自定义配置）

    删除旧表（overwrite）、建表、写入在同一事务内完成，任何一步失败都会整体回滚。
    """
    original_filename, file_bytes = _read_upload(file)
    logger.info("收到 CSV 上传: %s (%.2f MB)", original_filename, len(file_bytes) / (1024 * 1024))

    # ── 解析 ──
    try:
        parsed, excel_info = _parse_upload(
            original_filename, file_bytes, sheet_name,
            has_header=has_header,
            header_row=max(header_row, 0),
            data_start_row=data_start_row if data_start_row > 0 else None,
            skip_trailing_rows=skip_trailing,
            combine_header_rows=combine_headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    headers, rows = parsed.headers, parsed.rows

    if len(rows) > CSV_MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"CSV 行数 ({len(rows)}) 超过限制 ({CSV_MAX_ROWS})")

    # ── 列名处理：支持用户自定义列名 ──
    if column_names:
        try:
            custom_names = json.loads(column_names)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail="column_names 必须是 JSON 数组")
        if not isinstance(custom_names, list) or len(custom_names) != len(headers):
            raise HTTPException(
                status_code=400,
                detail=f"自定义列名数量({len(custom_names) if isinstance(custom_names, list) else '?'})与 CSV 列数({len(headers)})不一致",
            )
        headers = [str(name) for name in custom_names]

    used_names: set[str] = set()
    clean_headers = [sanitize_column_name(h, used_names) for h in headers]

    # ── 类型覆盖（用户给的是清理前或清理后的列名都能匹配） ──
    type_overrides: dict[str, str] = {}
    if column_types_override:
        try:
            raw_overrides = json.loads(column_types_override)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail="column_types_override 必须是 JSON 对象")
        if not isinstance(raw_overrides, dict):
            raise HTTPException(status_code=400, detail="column_types_override 必须是 JSON 对象")
        name_map = {raw: clean for raw, clean in zip(headers, clean_headers)}
        type_overrides = {name_map.get(k, k): v for k, v in raw_overrides.items()}

    if primary_key_column:
        pk_map = {raw: clean for raw, clean in zip(headers, clean_headers)}
        primary_key_column = pk_map.get(primary_key_column, primary_key_column)
        if primary_key_column not in clean_headers or primary_key_column in RESERVED_COLUMN_NAMES:
            raise HTTPException(status_code=400, detail=f"主键列 '{primary_key_column}' 不存在")

    # ── 表名处理 ──
    clean_table = normalize_table_name(table_name) if table_name and table_name.strip() else derive_table_name(_table_source_name(original_filename, excel_info))

    # ── 表名冲突 ──
    if table_exists(clean_table) and not overwrite:
        raise HTTPException(status_code=409, detail={
            "error": "table_exists",
            "table_name": clean_table,
            "message": f"表 '{clean_table}' 已存在。请设置 overwrite=true 覆盖。",
        })

    # ── 类型推断（支持覆盖，覆盖值必须在白名单内） ──
    try:
        column_types = infer_column_types(clean_headers, rows, type_overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ── 建表 + 写入（单事务） ──
    try:
        inserted = import_table(
            clean_table,
            column_types,
            clean_headers,
            rows,
            primary_key_column=primary_key_column,
            overwrite=overwrite,
            batch_size=CSV_BATCH_SIZE,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        reason = str(getattr(exc, "orig", None) or exc).strip()
        logger.error("CSV 导入失败（已回滚）: %s", reason)
        raise HTTPException(status_code=500, detail=f"导入失败，已回滚: {reason}")

    logger.info("CSV 导入成功: %s → %d 行", clean_table, inserted)
    auth.audit("csv_import", user, "table", clean_table,
               {"filename": original_filename, "sheet": excel_info.get("selected_sheet"), "rows": inserted, "overwrite": overwrite})
    return ImportResponse(
        table_name=clean_table,
        original_filename=original_filename,
        columns=column_types,
        row_count=inserted,
        message=f"成功导入 {inserted} 行数据到表 {clean_table}",
    )


@app.get("/api/tables", response_model=TableListResponse)
def list_tables(
    imported_only: bool = Query(False, description="仅显示导入的表"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    获取所有表信息（包含导入状态）

    可用于前端展示表列表，区分原始表和导入表。
    """
    table_list = [TableInfo(**table) for table in _load_table_details(imported_only)]

    return TableListResponse(tables=table_list)


@app.delete("/api/tables/{table_name}", response_model=DeleteResponse)
def delete_imported_table(table_name: str, user: CurrentUser = Depends(require_role("admin"))):
    """
    删除导入的表

    ---
    安全限制：仅允许删除 csv_ 前缀的导入表。
    原始表（fact_*、dim_* 等）不可通过此接口删除。
    """
    if not table_name.startswith("csv_"):
        raise HTTPException(status_code=403, detail="只能删除 csv_ 前缀的导入表")
    if not table_exists(table_name):
        raise HTTPException(
            status_code=404,
            detail=f"表 '{table_name}' 不存在",
        )

    try:
        drop_table(table_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"删除表失败: {str(exc)}",
        )

    logger.info("已删除导入表: %s", table_name)
    auth.audit("table_delete", user, "table", table_name)
    return DeleteResponse(
        deleted=table_name,
        message=f"表 {table_name} 已成功删除",
    )


# ---------------------------------------------------------------------------
# 直接运行入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
