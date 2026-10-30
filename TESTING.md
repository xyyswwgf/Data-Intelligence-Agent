# DataMind 测试指南

## 0. 自动化测试（推荐先跑）

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
```

```bash
.venv/bin/python -m pytest backend/tests -q
```

```bash
cd frontend && npm run lint && npm run build
```

- 集成测试使用**独立的测试库** `<DB_NAME>_test`（不存在时自动创建），自动执行迁移、写入虚构的门店样例数据
  （`backend/store_data/sample.py`），不会读写开发库里的真实门店数据。也可用 `TEST_DB_NAME` 指定库名，
  或设置 `DATABASE_URL` 使用自行准备的数据库。
- PostgreSQL（pgvector 镜像）未启动时，依赖数据库的用例自动跳过（显示为 skipped）。
- 管理员默认 admin / Admin12345（测试库中创建，可用 `ADMIN_PASSWORD` 覆盖）。
- 大模型调用全部使用脚本化的假模型，不消耗额度。

> 除 `/api/health`、`/api/auth/login` 外的接口都需要登录，下文的 curl 示例需要加上
> `-H "Authorization: Bearer $TOKEN"`，TOKEN 从 `POST /api/auth/login` 获取。

## 1. 启动服务

```bash
docker compose up -d postgres
```

```bash
ADMIN_PASSWORD=Admin12345 .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend && npm run dev
```

访问 `http://localhost:5173`。首次使用先导入门店数据（见 [STORE_DATA.md](STORE_DATA.md)）：

```bash
.venv/bin/python -m backend.store_data.loader --reset ~/门店报表/系统导出
```

没有真实数据时可以写入虚构样例数据：

```bash
.venv/bin/python -m backend.store_data.sample
```

## 2. 门店经营分析

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"question":"本月各门店经营怎么样？"}' | python3 -m json.tool
```

通过标准：

- `status` 为 `completed`，`steps` 依次为 planner、knowledge、sql、executor、analysis、attribution、visualization、report
- `metrics` 包含销售额、毛利、毛利率、手机台数，有任务数据时还有毛利目标和毛利达成率
- 各门店的销售额、毛利与门店报表「月度任务」表中的数字一致（有调回总库 / 批发的门店会略低，这些行不导入）
- `attribution.tables` 包含门店达成、产品线差距、店员业绩、产品线结构、库龄、收款方式
- `charts` 为每日趋势、门店毛利与目标、产品线毛利

推荐继续测试（关注点不同，关键发现的第一条随之变化）：

- 「滨河 8 月为什么没达成目标」：只分析滨河
- 「2026年7月城东的经营情况」：指定月份；没有上一个月数据时不显示环比
- 「哪些门店长库龄库存最多」「老城店员的业绩和提成」

历史与导出：`GET /api/analysis/history`、`GET /api/analysis/{run_id}/export?format=markdown|html`。

## 3. 知识库

- 知识库首页能看到「门店与数据说明」「门店月度任务」「门店指导书」及其子页面
- 知识问答：「店长店提的档位和点位是怎么规定的？」应给出四家门店的毛利档位和 6% / 8% / 9% 点位，并引用当月指导书
- 「8 月 X80 Pro Max 的提成是多少？」应命中产品政策中对应机型的小节

网页导入门店数据（知识库 → 导入门店数据）：

- 拖入销售台账、收款明细等 Excel，点「识别文件」：每个文件显示识别出的类型和行数，调回总库 / 批发的跳过提示、
  无法识别的文件都列出来，数据库此时没有变化
- 「确认导入」后显示写入行数和更新的知识库页面数；导入另一个月份的同名台账后，之前月份的销售和月度任务仍在
- 在「上传文档」中拖入 Excel 会自动打开导入门店数据；混合拖入时 Word / PDF 照常上传，Excel 给出转去导入的按钮
- 查看者角色调用 `POST /api/store-data/import` 返回 `403`

## 4. 导入表分析（CSV / Excel）

```bash
printf '日期,类别,销售额\n2026-01-01,A,100\n2026-01-15,B,80\n2026-02-01,A,120\n2026-02-10,B,70\n2026-03-01,A,90\n2026-03-10,B,60\n' > /tmp/agent_sales_test.csv
```

在「数据源 → 导入数据」中导入该文件，然后对导入表提问「为什么销售额下降？」：

- `sql_queries` 包含 `trend` 和 `group_compare`，`charts` 至少 2 个
- `report.summary` 能指出销售额从首期到末期下降

`POST /api/analyze/table` 传入非 `csv_` 前缀的表（如 `fact_store_sales`）应返回 `403`。

## 5. SQL 查询与安全

SQL 查询需要配置大模型，例如「每个门店 8 月的销售额和毛利」。

```bash
.venv/bin/python - <<'PY'
from backend.sql_guard import validate_sql
print(validate_sql('SELECT * FROM fact_store_sales'))
print(validate_sql('WITH x AS (SELECT 1) SELECT * FROM x'))
print(validate_sql('SELECT 1; DROP TABLE fact_store_sales;'))
print(validate_sql('DELETE FROM fact_store_sales'))
PY
```

预期前两个返回 `True`，后两个返回 `False`。

## 5.1 准确率评测

在「设置 → 准确率评测」中运行，或用命令行（输出每题结果和通过率）：

```bash
.venv/bin/python -m backend.evals.runner --set retail_store
.venv/bin/python -m backend.evals.runner --set retail_store --profile 2 --case store_profit --case top_staff
```

通过标准：用于正式使用的模型，门店零售评测集的通过率建议不低于 80%。未通过的题，在明细里对照「标准答案」和「回答摘录」，
判断是模型能力、提示词还是数据口径的问题。

## 6. 常见问题

`GET /` 返回 404：正常。`8000` 是 API 服务，前端访问 `http://localhost:5173`。

`5432` 端口被占用：项目已映射到 `15432`。

`docker compose up` 提示容器名 `data-agent-postgres` 已被占用：旧版本用 `postgres:16` 镜像创建过同名容器，
数据在数据卷中，删除旧容器后重新创建即可（数据保留）：

```bash
docker stop data-agent-postgres && docker rm data-agent-postgres
```

```bash
docker compose up -d postgres
```
