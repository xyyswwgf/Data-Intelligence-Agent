# Data Intelligence Agent 项目计划书

> 本文档描述经营分析 Agent 的业务数据模型与 API 契约。平台整体能力（智能助手、记忆、知识库、模型 / 工具 / 技能管理）
> 见 [README](README.md)、[ASSISTANT.md](ASSISTANT.md) 与 [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)。

> 版本：v2.0  
> 状态：Phase 1-4 已实现，CSV 通用分析 Agent 已接入  
> 技术栈：FastAPI + PostgreSQL + LangGraph + React + ECharts

## 1. 项目概述

本项目从 NL2SQL 查询工具升级为企业级数据智能 Agent。系统面向经营分析场景，支持用户用自然语言提出业务问题，Agent 自动完成分析规划、业务知识检索、SQL 查询、指标计算、归因分析、图表生成和分析报告输出。

核心链路：

```text
业务问题
-> FastAPI /api/analyze
-> LangGraph 工作流
-> PostgreSQL 多表查询
-> 指标和归因计算
-> ECharts 图表
-> 结构化经营报告
```

保留原 NL2SQL 能力：

```text
自然语言问题 -> LLM 生成 PostgreSQL SQL -> SQL Guard -> PostgreSQL -> 表格/图表
```

## 2. 业务数据模型

数据库：PostgreSQL 16  
默认端口：`15432`  
数据库名：`data_agent_db`

核心表：

| 表 | 说明 |
|---|---|
| `dim_date` | 日期维度 |
| `dim_region` | 大区、省份、城市 |
| `dim_product` | 商品、品类、成本、标价 |
| `dim_channel` | 官网、抖音、京东、天猫、线下门店 |
| `dim_customer` | 客户类型、会员等级、地区 |
| `fact_orders` | 订单事实表 |
| `fact_refunds` | 退款事实表 |
| `fact_marketing_spend` | 营销花费事实表 |
| `fact_daily_traffic` | 访问、加购、支付漏斗 |
| `knowledge_chunks` | 业务知识片段 |

核心指标：

- GMV
- 净销售额
- 订单数
- 客单价
- 商品成本
- 退款金额
- 退款率
- 毛利率
- 渠道 ROI
- 转化率
- 地区/品类贡献度

内置异常：

- 北京市场 2026-06 销售下降
- 华北抖音转化率下降
- 家用电器退款率升高
- 抖音 ROI 下降
- 净销售额和毛利率受退款与成本影响

## 3. 系统架构

```text
React + ECharts
    |
    v
FastAPI
    |
    |-- /api/query
    |     LLM -> PostgreSQL SQL -> SQL Guard -> Execute
    |
    |-- /api/analyze
    |     LangGraph:
    |     Planner -> Knowledge -> SQL -> Executor
    |     -> Analysis -> Attribution -> Visualization -> Report
    |
    |-- /api/analyze/table
    |     CSV Schema Profile -> Intent -> Generic SQL
    |     -> Analysis -> Visualization -> Report
    |
    |-- /api/import/csv/preview
    |     CSV 编码/列名/类型/主键/质量识别
    |
    |-- /api/import/csv
    |     PostgreSQL 建表和批量导入
    |
    v
PostgreSQL 16
```

FastAPI 和 LangGraph 的职责：

- FastAPI：HTTP API、CORS、参数校验、错误响应、文件上传
- LangGraph：Agent 工作流编排
- PostgreSQL：经营数据、导入表、知识片段
- React：分析工作台、图表、报告、导入预览

## 4. API 契约

### `POST /api/analyze`

请求：

```json
{
  "question": "本月销售额为什么下降？",
  "analysis_mode": "auto"
}
```

响应包含：

- `steps`：Agent 节点执行记录
- `plan`：分析计划
- `knowledge`：检索到的业务知识
- `sql_queries`：执行的 SQL 和结果
- `metrics`：核心指标
- `attribution`：地区、品类、退款、ROI、转化率归因
- `charts`：ECharts 配置
- `report`：摘要、关键发现、建议动作

### `POST /api/analyze/table`

请求：

```json
{
  "question": "为什么销售额下降？",
  "table_name": "csv_sales",
  "analysis_mode": "auto"
}
```

说明：

- 只允许分析 `csv_` 前缀导入表。
- 自动识别时间列、数值指标列、分类维度列和 ID 列。
- 支持趋势、分组对比、缺失率、异常候选和结构画像。
- 如果字段不足以做强归因，报告必须说明证据不足，不伪造业务结论。

### `POST /api/import/csv/preview`

返回：

- 编码和分隔符
- 原始列名和清洗列名
- 类型推断
- 样本值
- 主键候选
- 推荐主键
- 语义列识别
- 空值比例
- 重复行数量
- 建议表名

### `POST /api/query`

保留 NL2SQL 能力，需要 LLM API Key。只允许 SELECT/CTE 查询。

## 5. 前端设计

前端默认进入“分析 Agent”模式：

- 顶部自然语言输入
- 模式切换：分析 Agent / SQL 查询
- 指标卡片
- Agent 执行步骤
- ECharts 图表
- 归因明细表
- 分析报告
- SQL 执行记录
- CSV 自动识别和导入弹窗
- 导入表旁提供“Agent 分析”入口
- 数据源信息面板

## 6. 安全设计

- LLM SQL 结果必须经过 `sql_guard.py`
- 只允许 `SELECT`、`EXPLAIN SELECT`、`WITH ... SELECT`
- 拦截 `DROP`、`DELETE`、`UPDATE`、`INSERT`、`ALTER` 等写操作
- CSV 导入的 DDL/INSERT 由服务端构造
- 表名和列名使用 PostgreSQL 双引号安全转义
- 仅允许删除 `csv_` 前缀导入表
- `/api/analyze/table` 仅允许读取 `csv_` 前缀导入表

## 7. 当前完成情况

已完成：

- PostgreSQL 迁移
- 企业经营分析数据模型
- LangGraph 分析工作流
- 多 SQL 查询分析
- 指标计算
- 地区/品类/退款/ROI/转化率归因
- ECharts 图表输出
- React 分析工作台
- CSV 文件自动识别基础版
- CSV 导入 PostgreSQL
- CSV 通用分析 Agent
- Markdown / HTML 轻量报告导出

下一阶段：

- RAG 增强：使用 pgvector 替换关键词检索
- Excel 导入
- 分析历史持久化
- 更完整的测试覆盖
