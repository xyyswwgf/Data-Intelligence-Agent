# Data Intelligence Agent 测试指南

## 0. 自动化测试（推荐先跑）

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest backend/tests -q
cd frontend && npm run lint && npm run build
```

API 集成测试需要 PostgreSQL 已启动；未启动时会自动跳过（显示为 skipped）。

## 1. 启动服务

启动 PostgreSQL：

```bash
cd /Users/xy/PycharmProjects/PythonProject
docker compose up -d
```

确认容器健康：

```bash
docker compose ps
```

预期看到：

```text
data-agent-postgres ... healthy ... 15432->5432
```

安装依赖：

```bash
.venv/bin/pip install -r backend/requirements.txt
```

启动后端：

```bash
DB_PORT=15432 .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

启动前端：

```bash
cd frontend
npm run dev
```

访问：

```text
http://localhost:5173
```

## 2. 基础 API 测试

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

数据源信息：

```bash
curl http://127.0.0.1:8000/api/info
```

预期：

- `database.engine` 为 `PostgreSQL 16`
- 存在 `dim_` 和 `fact_` 表
- `fact_orders` 有数千行数据

## 3. Agent 分析测试

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"question":"本月销售额为什么下降？","analysis_mode":"auto"}' \
  | python3 -m json.tool
```

通过标准：

- `status` 为 `completed`
- `steps` 包含 `planner`、`knowledge`、`sql`、`executor`、`analysis`、`attribution`、`visualization`、`report`
- `metrics` 包含 GMV、净销售额、退款金额、毛利率
- `attribution.region` 能看到北京下降
- `charts` 至少 3 个
- `report.summary` 有明确结论
- 前端报告区提供 Markdown / HTML 导出入口

推荐继续测试：

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"question":"哪些渠道 ROI 变差？"}'

curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"question":"哪些商品退款率异常升高？"}'
```

历史和导出测试：

```bash
RUN_ID=$(curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"question":"哪些渠道 ROI 变差？"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')

curl -s http://127.0.0.1:8000/api/analysis/history | python3 -m json.tool
curl -s "http://127.0.0.1:8000/api/analysis/$RUN_ID/export?format=markdown"
curl -s "http://127.0.0.1:8000/api/analysis/$RUN_ID/export?format=html" | head
```

## 4. CSV 自动识别测试

预览：

```bash
curl -s -F file=@sample_data/example_students.csv \
  http://127.0.0.1:8000/api/import/csv/preview \
  | python3 -m json.tool
```

通过标准：

- 返回 `file_profile`
- `file_profile.file_type` 为 `csv`
- `file_profile.recommended_primary_key` 应优先识别为 `学号`
- 返回语义列、空值比例、重复行数量和建议表名

导入：

```bash
curl -s -F file=@sample_data/example_students.csv \
  -F table_name=phase_check \
  -F primary_key_column=学号 \
  -F overwrite=true \
  http://127.0.0.1:8000/api/import/csv
```

查看导入表：

```bash
curl 'http://127.0.0.1:8000/api/tables?imported_only=true'
```

删除测试表：

```bash
curl -X DELETE http://127.0.0.1:8000/api/tables/csv_phase_check
```

## 5. CSV 通用分析 Agent 测试

对已导入的学生表测试缺失率分析：

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze/table \
  -H 'Content-Type: application/json' \
  -d '{"table_name":"csv_phase_check","question":"哪些字段缺失比较严重？","analysis_mode":"auto"}' \
  | python3 -m json.tool
```

通过标准：

- `status` 为 `completed`
- `steps` 包含 `schema_profiler`、`intent`、`generic_sql`、`executor`、`generic_analysis`、`visualization`、`report`
- `sql_queries` 至少包含 `overview` 和 `missing`
- `report.summary` 明确指出缺失最严重字段

趋势/分组测试：

```bash
printf '日期,类别,销售额\n2026-01-01,A,100\n2026-01-15,B,80\n2026-02-01,A,120\n2026-02-10,B,70\n2026-03-01,A,90\n2026-03-10,B,60\n' > /tmp/agent_sales_test.csv

curl -s -F file=@/tmp/agent_sales_test.csv \
  -F table_name=agent_sales_test \
  -F overwrite=true \
  http://127.0.0.1:8000/api/import/csv

curl -s -X POST http://127.0.0.1:8000/api/analyze/table \
  -H 'Content-Type: application/json' \
  -d '{"table_name":"csv_agent_sales_test","question":"为什么销售额下降？","analysis_mode":"auto"}' \
  | python3 -m json.tool

curl -s -X DELETE http://127.0.0.1:8000/api/tables/csv_agent_sales_test
```

通过标准：

- `sql_queries` 包含 `trend` 和 `group_compare`
- `charts` 至少 2 个
- `report.summary` 能指出销售额从首期到末期下降

安全测试：

```bash
curl -i -s -X POST http://127.0.0.1:8000/api/analyze/table \
  -H 'Content-Type: application/json' \
  -d '{"table_name":"fact_orders","question":"测试"}'
```

预期返回 `403`，因为只允许分析 `csv_` 前缀导入表。

## 6. NL2SQL 查询测试

该接口需要 LLM API Key。

DeepSeek 示例：

```bash
DB_PORT=15432 \
DEEPSEEK_API_KEY=你的key \
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic \
LLM_MODEL=deepseek-v4-pro \
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

测试：

```bash
curl -s -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"按月份统计 GMV 和订单数"}'
```

## 7. SQL 安全测试

危险 SQL 应被拒绝：

```bash
python3 - <<'PY'
from backend.sql_guard import validate_sql
print(validate_sql('SELECT * FROM fact_orders'))
print(validate_sql('WITH x AS (SELECT 1) SELECT * FROM x'))
print(validate_sql('SELECT 1; DROP TABLE fact_orders;'))
print(validate_sql('DELETE FROM fact_orders'))
PY
```

预期：

- 前两个返回 `True`
- 后两个返回 `False`

## 8. 前端测试

执行构建：

```bash
cd frontend
npm run build
```

手动检查：

- 默认标题为“数据智能 Agent 工作台”
- 模式切换可在“分析 Agent”和“SQL 查询”之间切换
- 输入“本月销售额为什么下降？”后展示指标卡、步骤、图表、归因表、报告和 SQL 记录
- 输入“哪些渠道 ROI 变差？”和“哪些商品退款率异常升高？”时，报告摘要和建议会按问题重点变化
- 最近分析区域会出现历史记录，点击可回看结果
- 报告区“导出 MD / 导出 HTML”链接可下载当前报告
- CSV 上传弹窗展示自动识别结果，包括推荐主键和建议表名
- 已导入表旁有“Agent 分析”按钮
- 点击“Agent 分析”后，输入框提示变为针对该 `csv_` 表提问
- 对导入表提问“哪些字段缺失比较严重？”能展示步骤、SQL、图表和报告

## 9. 常见问题

`GET /` 返回 404：

- 正常。`8000` 是 FastAPI API 服务，不提供前端页面。前端访问 `http://localhost:5173`。

`5432` 端口被占用：

- 项目已映射到 `15432`，后端启动时使用 `DB_PORT=15432`。

看到 orphan `nl2sql-mysql`：

- 旧 MySQL 容器残留，不影响当前 PostgreSQL。可执行：

```bash
docker compose up -d --remove-orphans
```
