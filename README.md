# Data Intelligence Agent

企业级数据智能 Agent：面向经营分析场景，支持自然语言提问、Agent 自动规划、多条 PostgreSQL 查询、指标计算、归因分析、ECharts 图表和结构化报告。

当前版本已经从原 NL2SQL MVP 升级到：

- FastAPI 后端
- PostgreSQL 经营分析数据仓库
- LangGraph 分析工作流
- React + Vite + ECharts 前端工作台
- CSV 自动识别、预览、导入和表管理

## 核心能力

- `/api/query`：保留原自然语言转 SQL 查询能力，需要配置 LLM API Key。
- `/api/analyze`：经营分析 Agent，不依赖 LLM，自动执行规划、SQL、指标、归因、图表和报告。
- `/api/analyze/table`：CSV 导入表通用分析 Agent，自动识别字段角色并生成表级分析。
- `/api/analysis/history`：查看最近 20 次分析历史，当前为内存存储，重启后清空。
- `/api/analysis/{run_id}/export`：导出单次分析报告，支持 Markdown 和 HTML。
- `/api/import/csv/preview`：自动识别 CSV 文件编码、列名、字段类型、主键候选、语义列、空值比例、重复行和建议表名。
- `/api/import/csv`：导入 CSV 到 PostgreSQL，支持手动确认列名、类型、主键和表名。
- `/api/tables`：查看导入表和系统表。

## 数据模型

初始化数据位于 `backend/init_db.sql`，当前包含 2026 年 1-6 月模拟经营数据。

核心表：

- `dim_date`：日期维度
- `dim_region`：大区、省份、城市
- `dim_product`：商品、品类、成本、标价
- `dim_channel`：官网、抖音、京东、天猫、线下门店
- `dim_customer`：客户类型、会员等级、地区
- `fact_orders`：订单事实表
- `fact_refunds`：退款事实表
- `fact_marketing_spend`：营销投放事实表
- `fact_daily_traffic`：流量和转化漏斗事实表
- `knowledge_chunks`：业务知识片段，用于关键词检索版 RAG

内置异常场景：

- 2026-06 北京市场销售下降
- 华北抖音转化率下降
- 家用电器退款率升高
- 抖音渠道 ROI 下降
- 净销售额和毛利率受退款与成本共同影响

## 启动方式

在项目根目录执行：

```bash
cd /Users/xy/PycharmProjects/PythonProject
```

安装后端依赖：

```bash
.venv/bin/pip install -r backend/requirements.txt
```

启动 PostgreSQL：

```bash
docker compose up -d
```

后端默认连接：

```text
host: 127.0.0.1
port: 15432
database: data_agent_db
user: data_agent
password: Simple@123
```

启动后端：

```bash
DB_PORT=15432 .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

> 注意：如果 shell 里残留了旧 MySQL 版本的环境变量（如 `DB_PORT=3306`、`DB_USER=root`、`DB_NAME=nl2sql_db`），
> 它们会覆盖默认值导致连接失败。可以 `unset DB_PORT DB_USER DB_NAME DB_PASSWORD DB_HOST`，
> 或复制 `.env.example` 为 `.env` 统一管理配置（`.env` 已被 gitignore）。

启动前端：

```bash
cd frontend
npm run dev
```

访问：

```text
http://localhost:5173
```

## LLM 配置

`/api/analyze` 不依赖 LLM，可以直接运行。

`/api/query` 需要配置 Anthropic-compatible API Key。DeepSeek 示例：

```bash
DB_PORT=15432 \
DEEPSEEK_API_KEY=你的key \
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic \
LLM_MODEL=deepseek-v4-pro \
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

也支持：

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_AUTH_TOKEN`
- `DEEPSEEK_API_KEY`

## 安全设计（纵深防御）

| 层级 | 位置 | 作用 |
|------|------|------|
| 1 | `llm_service.py` System Prompt | 约束模型只生成单条 SELECT |
| 2 | `sql_guard.py` | 屏蔽字符串字面量后做语法骨架扫描：仅放行 SELECT/WITH/EXPLAIN，拦截 DDL/DML、`SELECT INTO`、`COPY`、`pg_sleep`、`pg_read_file`、`dblink`、语句堆叠等 |
| 3 | `database.execute_readonly_sql` | `READ ONLY` 事务 + `statement_timeout` + 返回行数上限，即使前两层被绕过数据库也拒绝写入 |

CSV 导入侧：列类型（含用户手动覆盖）必须命中白名单，表名/列名按 PostgreSQL 63 字节上限截断，
覆盖导入的「删旧表 → 建表 → 写入」在同一事务内完成，失败整体回滚。

`/api/query` 在 SQL 执行报错时，会把报错回传给 LLM 自动修正（次数由 `LLM_SQL_REPAIR_ATTEMPTS` 控制，默认 1 次）。

## 推荐问题

在“分析 Agent”模式下可以直接问：

- 本月销售额为什么下降？
- 哪些地区拖累了整体利润？
- 哪些渠道 ROI 变差？
- 哪些商品退款率异常升高？
- 华北区域高价值用户最近购买情况怎么样？
- 2026年5月销售额怎么样？（问题中写明月份时，按该月 vs 上一月分析；否则自动取数据中的最新月份）

## API 示例

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

数据源信息：

```bash
curl http://127.0.0.1:8000/api/info
```

Agent 分析：

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"question":"本月销售额为什么下降？","analysis_mode":"auto"}'
```

CSV 导入表通用分析：

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze/table \
  -H 'Content-Type: application/json' \
  -d '{"table_name":"csv_students","question":"哪些字段缺失比较严重？","analysis_mode":"auto"}'
```

查看分析历史：

```bash
curl -s http://127.0.0.1:8000/api/analysis/history
```

导出报告，把命令中的 `RUN_ID` 替换为分析结果里的 `run_id`：

```bash
curl -L 'http://127.0.0.1:8000/api/analysis/RUN_ID/export?format=markdown'
curl -L 'http://127.0.0.1:8000/api/analysis/RUN_ID/export?format=html'
```

CSV 自动识别：

```bash
curl -s -F file=@sample_data/example_students.csv \
  http://127.0.0.1:8000/api/import/csv/preview
```

CSV 导入：

```bash
curl -s -F file=@sample_data/example_students.csv \
  -F table_name=students \
  -F primary_key_column=学号 \
  -F overwrite=true \
  http://127.0.0.1:8000/api/import/csv
```

## 架构

```text
React/Vite Frontend
        |
        v
FastAPI Backend
        |
        |-- /api/query     -> LLM NL2SQL -> SQL Guard -> PostgreSQL
        |-- /api/analyze   -> LangGraph Agent Workflow
        |-- /api/analyze/table -> CSV Schema Profile -> Generic SQL -> Report
        |-- /api/import/*  -> CSV Parser/Profile/Importer
        |
        v
PostgreSQL 16
```

LangGraph 节点：

```text
Planner -> Knowledge -> SQL -> Executor -> Analysis -> Attribution -> Visualization -> Report
```

## 当前阶段

已完成：

- Phase 1：PostgreSQL 迁移与现有功能保活
- Phase 2：企业经营分析数据模型
- Phase 3：LangGraph 分析工作流
- Phase 4 基础版：归因分析结果前后端展示
- CSV 自动识别基础版
- CSV 通用分析 Agent：导入表字段识别、趋势/分组/缺失/异常分析
- 分析历史基础版：内存记录最近分析，可在前端回看
- 报告导出基础版：Markdown / HTML

后续建议：

- 增强 RAG：把 `knowledge_chunks` 升级为 pgvector 检索
- 支持 Excel 文件导入
- 增加分析历史持久化表（当前为进程内有界存储，默认保留最近 100 条）
- 经营分析 Agent 目前是确定性模板（可复现、可审计）；可选地引入 LLM 做规划和报告措辞，模板 SQL 作为兜底
- 增加前端 E2E 测试和截图验证

## 自动化测试

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest backend/tests -q
```

- `test_sql_guard.py` / `test_csv_importer.py`：纯函数单元测试，无需数据库
- `test_api.py`：API 集成测试，需要 `docker compose up -d`；LLM 调用全部 mock，不消耗额度。数据库不可用时自动跳过

## 当前 Demo 边界

系统现在有两类 Agent：

- 内置经营分析 Agent：基于模拟企业数据仓库，适合展示完整业务归因。
- CSV 通用分析 Agent：基于上传表自动识别字段，适合真实 CSV 的趋势、分组、缺失、异常和结构画像分析。

CSV 通用分析不会伪装成完整业务专家。如果字段不足以回答严格的“为什么”，报告会说明证据不足，并给出可继续补充的字段方向。
