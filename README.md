# DataMind · 数据智能 Agent

面向企业私有化部署的数据智能平台：用自然语言做经营分析、查询业务数据，并把企业的制度、口径、手册沉淀为可检索、可引用的知识库（RAG + Wiki）。

## 核心能力

| 模块 | 能力 |
|------|------|
| 经营分析 Agent | LangGraph 工作流：规划 → 检索企业知识 → 多条只读 SQL → 指标计算 → 地区/品类/渠道归因 → ECharts 图表 → 结构化报告（可导出 Markdown / HTML） |
| CSV 通用分析 | 上传 CSV 自动识别编码、表头、字段类型、主键候选；导入后自动识别时间列 / 指标列 / 维度列做趋势、分组、缺失、异常分析 |
| 自然语言查询 | LLM 生成 SQL → 安全校验 → 只读执行；执行报错时自动把错误回传给 LLM 修正 |
| 企业知识库 | Markdown / TXT / PDF / Word 统一转换为 Wiki 页面；目录树、标签索引、`[[双向链接]]`、版本历史与回滚、乐观锁防覆盖 |
| 知识问答（RAG） | 向量（pgvector + bge-m3）+ 关键词混合检索，RRF 融合排序；回答逐条标注来源页面，可点击核对原文；未配置 LLM 时返回抽取式答案 |
| 企业级管理 | 账号登录、三级角色权限（只读成员 / 编辑 / 管理员）、登录失败锁定、操作审计日志、系统状态与索引运维 |

设计细节见 [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)。

## 快速部署（Docker Compose）

```bash
cp .env.example .env        # 修改数据库密码、ADMIN_PASSWORD、LLM Key 等
docker compose --profile local-embedding up -d --build
```

首次启动后拉取本地向量模型（免费、数据不出网）：

```bash
docker compose exec ollama ollama pull bge-m3
```

国内网络无法访问 Ollama 官方仓库时，改用 ModelScope 镜像：

```bash
docker compose exec ollama ollama pull modelscope.cn/gpustack/bge-m3-GGUF
```

```bash
docker compose exec ollama ollama cp modelscope.cn/gpustack/bge-m3-GGUF bge-m3
```

访问 `http://<服务器>:8080`，用 `.env` 中的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录（未设置密码时，随机密码打印在后端日志：`docker compose logs backend`）。

不想在本机跑模型时，把 `.env` 的 Embedding 改为硅基流动云端 `BAAI/bge-m3`（免费额度，与本地 bge-m3 同一向量空间，切换无需重建索引），并去掉 `--profile local-embedding`。

### 升级

替换代码后重新 `docker compose up -d --build` 即可。数据库迁移在后端启动时自动执行（`backend/migrations.py`，带 advisory lock，多实例安全）。

## 本地开发

```bash
docker compose up -d postgres
```

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
```

```bash
ADMIN_PASSWORD=Admin12345 .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend && npm install && npm run dev
```

访问 `http://localhost:5173`。

> 如果 shell 里残留旧 MySQL 版本的环境变量（如 `DB_PORT=3306`、`DB_USER=root`），会覆盖默认配置导致连不上库，
> 请 `unset DB_PORT DB_USER DB_NAME DB_PASSWORD DB_HOST` 或使用 `.env`。

## 账号与权限

| 角色 | 可以做什么 |
|------|-----------|
| 只读成员 `viewer` | 经营分析、SQL 查询、知识问答、浏览/搜索 Wiki；只能看到自己的分析历史 |
| 编辑 `editor` | 以上全部 + 上传文档、新建/编辑 Wiki 页面、恢复历史版本、导入 CSV |
| 管理员 `admin` | 以上全部 + 删除页面和数据表、用户管理、审计日志、系统状态、重建索引、查看所有人的分析历史 |

## 安全设计

| 层级 | 位置 | 作用 |
|------|------|------|
| 1 | `llm_service.py` System Prompt | 约束模型只生成单条 SELECT |
| 2 | `sql_guard.py` | 屏蔽字面量后扫描 SQL 骨架：仅放行 SELECT/WITH/EXPLAIN，拦截 DDL/DML、`SELECT INTO`、`COPY`、`pg_sleep`、`pg_read_file`、`dblink`、语句堆叠 |
| 3 | `database.execute_readonly_sql` | `READ ONLY` 事务 + `statement_timeout` + 行数上限 |
| 4 | 数据库角色 `nl2sql_reader` | LLM / Agent SQL 以只读角色执行，只对业务表有 SELECT 权限，读不到账号、审计、知识库等系统表 |

其他：
- 密码 scrypt 哈希；会话 token 只存哈希、可过期、停用/改密后立即吊销；连续 5 次密码错误锁定 15 分钟
- 上传文件类型白名单 + 大小上限 + sha256 去重；Markdown 渲染经 DOMPurify 清洗
- CSV 导入列类型白名单（防 DDL 注入）、标识符按 63 字节截断、覆盖导入单事务回滚
- 登录、上传、编辑、删除、导入、问答、用户变更全部写入审计日志

## API

完整接口文档：`http://localhost:8000/api/docs`。除 `/api/health` 与 `/api/auth/login` 外，所有接口需要请求头 `Authorization: Bearer <token>`。

```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"<密码>"}'
```

| 分组 | 主要接口 |
|------|---------|
| 认证 | `POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`、`POST /api/auth/change-password` |
| 分析 | `POST /api/analyze`、`POST /api/analyze/table`、`POST /api/query`、`GET /api/analysis/history`、`GET /api/analysis/{run_id}/export` |
| 数据 | `POST /api/import/csv/preview`、`POST /api/import/csv`、`GET /api/tables`、`DELETE /api/tables/{name}` |
| 知识库 | `GET /api/wiki/index`、`GET /api/wiki/tree`、`GET /api/wiki/search`、`GET/POST/PUT/DELETE /api/wiki/pages`、`GET /api/wiki/pages/{id}/versions`、`POST /api/kb/documents`、`POST /api/kb/ask` |
| 管理 | `GET/POST/PATCH /api/admin/users`、`GET /api/admin/audit`、`GET /api/admin/system`、`POST /api/admin/kb/reindex` |

## 数据模型

经营分析演示数据位于 `backend/init_db.sql`（2026 年 1–6 月模拟电商数据，内置 6 月北京下滑、家电退款率升高、华北抖音转化下降等异常），正式交付客户时可在 `docker-compose.yml` 中去掉该挂载。

系统表（`users`、`auth_sessions`、`audit_logs`、`wiki_*`、`kb_*`、`agent_runs`）由迁移创建，不暴露给 LLM 和数据表列表。

## 架构

```text
React / Vite 前端（Nginx）
        │  /api
        ▼
FastAPI 后端 ── auth / audit ── 角色鉴权、审计日志
        ├── /api/analyze        LangGraph 经营分析 Agent ──┐
        ├── /api/analyze/table  CSV 通用分析 Agent          │ 只读角色 + READ ONLY 事务
        ├── /api/query          LLM NL2SQL + SQL Guard ─────┘
        ├── /api/wiki, /api/kb  Wiki、文档入库、混合检索、RAG 问答
        └── migrations          启动时自动迁移
        │
        ▼
PostgreSQL 16 + pgvector          Ollama (bge-m3) / 云端 Embedding        DeepSeek / Anthropic 兼容 LLM
```

## 自动化测试

```bash
.venv/bin/python -m pytest backend/tests -q
```

```bash
cd frontend && npm run lint && npm run build
```

- `test_sql_guard.py` / `test_csv_importer.py` / `test_kb_units.py`：纯函数单元测试，无需数据库
- `test_api.py` / `test_kb_auth.py`：API 集成测试，需要 PostgreSQL（含 pgvector）；LLM 与 Embedding 默认关闭/mock，不消耗额度。数据库不可用时自动跳过
- 测试会在数据库中创建 `test_*` 用户和临时页面，建议使用独立的测试库（设置 `DB_PORT` / `DB_NAME` 指向测试库）

## 后续规划

- 扫描件 PDF 的 OCR、Excel / PPT 导入
- 部门级空间权限（页面 ACL）、企业 SSO（LDAP / OIDC）
- Rerank 模型（bge-reranker）进一步提升检索精度
- 经营分析 Agent 引入 LLM 做规划与报告措辞，模板 SQL 作为可审计兜底
