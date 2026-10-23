# DataMind · 数据智能 Agent

> 面向企业私有化部署的数据智能平台：用一句话提问，Agent 自主查知识库、找数据表、写只读 SQL、做归因分析，并记住每位用户的偏好。

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
![License](https://img.shields.io/badge/License-Apache%202.0-blue)

## 它能做什么

| 能力 | 说明 |
|------|------|
| **智能助手（推理 Agent）** | ReAct 推理循环：自主规划 → 调用工具 → 观察结果 → 自我修正 → 带证据作答。SQL 写错会根据报错自己改；回答前校验引用，编造的来源会被去掉。思考过程、每次工具调用和上下文占用实时流式展示 |
| **模型自由切换** | 在管理页接入多个本地模型（Ollama / vLLM）和云端 API（DeepSeek、通义千问、Claude 等），用户按对话切换；API Key 加密存储；一键测试延迟和是否支持工具调用 |
| **工具与技能** | 内置知识库检索、表结构、只读 SQL、经营归因、CSV 分析等工具，可按角色启停；管理员可在界面上新增 **SQL 模板工具**（固化业务口径）和 **HTTP 接口工具**（接入 CRM、工单等系统）；**技能**封装可复用的做事方法，按需加载 |
| **记忆与上下文** | 会话持久化、长对话自动压缩为滚动摘要；用户长期记忆（偏好 / 事实 / 目标 / 指令）自动提炼、去重合并、语义召回，用户可查看和管理；按 token 预算组装上下文 |
| **企业知识库** | Markdown / TXT / PDF / Word 统一转为 Wiki 页面：目录树、标签索引、`[[双向链接]]`、版本历史与回滚；向量（pgvector + bge-m3）+ 关键词混合检索，问答逐条标注来源 |
| **经营分析** | LangGraph 工作流：多条只读 SQL → 指标计算 → 地区 / 品类 / 渠道归因 → ECharts 图表 → 结构化报告（可导出 Markdown / HTML） |
| **CSV 数据接入** | 自动识别编码、表头、字段类型、主键候选；导入后自动做趋势、分组、缺失、异常分析 |
| **企业级管理** | 账号与三级角色、登录失败锁定、操作审计日志、系统状态与索引运维、Docker 一键私有化部署 |

## 架构

```text
┌──────────────────────── React 19 + Vite + ECharts（Nginx）────────────────────────┐
│  对话（SSE 推理轨迹） │ 知识库 Wiki │ 我的记忆 │ 管理：模型 / 工具 / 技能 / 用户 / 审计  │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │ /api
┌─────────────────────────────────────────▼─────────────────────────────────────────┐
│ FastAPI   认证 · 角色鉴权 · 审计                                                   │
│                                                                                   │
│  智能助手 ── ReAct 循环 ──► 工具注册表 ──► 知识库检索 / 表结构 / 只读 SQL /          │
│     │                       （按启用状态      经营归因 / CSV 分析 / 记忆 / 技能 /     │
│     │                         和角色过滤）     SQL 模板工具 / HTTP 接口工具          │
│     ├── 上下文管理：系统指令 + 核心记忆 + 相关记忆 + 会话摘要 + 最近对话（token 预算） │
│     └── LLM 网关：Anthropic / OpenAI 兼容协议，多模型配置（本地 ↔ 云端）             │
│                                                                                   │
│  经营分析 Agent（LangGraph） · CSV 通用分析 · NL2SQL · RAG 问答 · 文档入库与索引     │
│                                                                                   │
│  安全网关：sql_guard 语法骨架校验 → 只读数据库角色 → READ ONLY 事务 + 超时 + 行数上限 │
└──────────────┬──────────────────────────────┬──────────────────────┬──────────────┘
               ▼                              ▼                      ▼
     PostgreSQL 16 + pgvector        Ollama / 云端 Embedding      本地 / 云端大模型
  业务数据 · 知识库 · 会话 · 记忆           （bge-m3）
```

## 快速开始（Docker Compose）

```bash
cp .env.example .env    # 修改数据库密码、ADMIN_PASSWORD、APP_SECRET_KEY
```

```bash
docker compose --profile local-embedding up -d --build
```

拉取本地向量模型（免费、数据不出网）：

```bash
docker compose exec ollama ollama pull bge-m3
```

国内网络访问不了 Ollama 官方仓库时，改用 ModelScope 镜像：

```bash
docker compose exec ollama ollama pull modelscope.cn/gpustack/bge-m3-GGUF
```

```bash
docker compose exec ollama ollama cp modelscope.cn/gpustack/bge-m3-GGUF bge-m3
```

打开 `http://<服务器>:8080`，用 `.env` 中的管理员账号登录，然后在 **系统管理 → 模型** 中添加大模型（本地 Ollama、DeepSeek 等都有预设），点「测试连接」确认可用即可开始对话。

> 未设置 `ADMIN_PASSWORD` 时，首次启动会生成随机密码并打印在后端日志：`docker compose logs backend`。
> `APP_SECRET_KEY` 用于加密保存的 API Key，请妥善备份；更换后需重新填写已保存的 Key。

升级时替换代码后重新执行 `docker compose up -d --build`，数据库迁移在后端启动时自动完成。

## 本地开发

```bash
docker compose up -d postgres
```

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
```

```bash
ADMIN_PASSWORD=Admin12345 .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend && npm install && npm run dev
```

访问 `http://localhost:5173`。用本地模型开发时，可直接用环境变量指定（也可以登录后在管理页添加）：

```bash
LLM_PROVIDER=openai_compatible LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen3.5:4b-mlx LLM_NO_REASONING_EFFORT=none .venv/bin/uvicorn backend.main:app --port 8000
```

> 如果 shell 里残留旧版本的 `DB_PORT=3306`、`DB_USER=root` 等环境变量，会覆盖默认配置导致连不上库，请先 `unset` 或改用 `.env`。

## 角色与权限

| 角色 | 权限 |
|------|------|
| 只读成员 `viewer` | 对话（智能助手 / 经营分析 / 知识问答 / SQL 查询）、浏览与搜索知识库、管理自己的记忆；只能看到自己的会话 |
| 编辑 `editor` | 以上全部 + 上传文档、编辑 Wiki、恢复历史版本、导入 CSV |
| 管理员 `admin` | 以上全部 + 模型 / 工具 / 技能管理、用户管理、审计日志、删除页面和数据表、重建索引 |

工具还可以单独设置最低使用角色，例如把访问 CRM 的 HTTP 工具限定为管理员。

## 安全设计

| 层级 | 机制 |
|------|------|
| SQL 生成 | Prompt 约束只生成单条 SELECT |
| SQL 校验 | `sql_guard` 屏蔽字面量后扫描语法骨架：拦截 DDL/DML、`SELECT INTO`、`COPY`、`pg_sleep`、`pg_read_file`、`dblink`、语句堆叠 |
| 数据库权限 | LLM / Agent / SQL 模板工具都以只读角色 `nl2sql_reader` 执行，读不到账号、审计、会话、记忆、知识库等系统表 |
| 执行约束 | `READ ONLY` 事务 + `statement_timeout` + 返回行数上限 |
| 自定义工具 | SQL 模板参数由驱动绑定（注入无效）；HTTP 工具 URL 固定、路径参数编码、默认禁止访问内网（防 SSRF）、凭证加密存储 |
| 账号 | scrypt 密码哈希；会话 token 只存哈希、可过期可吊销；连续 5 次失败锁定 15 分钟 |
| 数据 | API Key / 工具凭证 Fernet 加密；上传文件类型白名单、大小上限、sha256 去重；Markdown 渲染经 DOMPurify 清洗 |
| 审计 | 登录、上传、编辑、删除、导入、对话、模型 / 工具 / 技能变更全部记录 |

## 配置

完整配置见 [`.env.example`](.env.example)，常用项：

| 变量 | 说明 |
|------|------|
| `DB_*` | PostgreSQL 连接 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 首次启动时创建的管理员 |
| `APP_SECRET_KEY` | 加密 API Key 和工具凭证的密钥 |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 兜底模型（管理页未配置模型时使用） |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` | 向量模型，默认本地 Ollama `bge-m3`，可切换到硅基流动云端（同一向量空间，无需重建索引） |
| `AGENT_MAX_STEPS` | 智能助手单轮最多工具调用步数 |
| `MEMORY_ENABLED` / `CONTEXT_COMPACT_RATIO` | 记忆开关、会话压缩阈值 |
| `TOOL_HTTP_ALLOWED_HOSTS` | HTTP 工具允许访问的内网主机 |

## 项目结构

```text
backend/
  main.py               FastAPI 入口：分析、查询、CSV 导入、数据表
  assistant/            智能助手：推理循环、工具注册表、自定义工具、技能、记忆、会话、上下文管理
  kb/                   知识库：文档转换、切块、向量化、混合检索、RAG、Wiki
  routers/              认证、对话与记忆、知识库、模型 / 工具 / 技能管理 API
  llm_gateway.py        LLM 网关（Anthropic / OpenAI 兼容，多模型配置）
  llm_profiles.py       模型配置管理（加密 Key、连通性测试、模型发现）
  agent_service.py      经营分析 Agent（LangGraph）
  generic_table_agent.py CSV 通用分析 Agent
  sql_guard.py          SQL 安全校验
  migrations.py         启动时自动执行的数据库迁移
  tests/                pytest 测试
frontend/src/
  components/           对话、推理轨迹、知识库 Wiki、记忆、管理页（模型 / 工具 / 技能）
deploy/                 后端 / 前端 Dockerfile、Nginx 配置
```

## 文档

- [ASSISTANT.md](ASSISTANT.md)：智能助手的推理架构、记忆分层、上下文预算、模型切换、工具与技能
- [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)：知识库与 RAG 设计
- [USER_GUIDE.md](USER_GUIDE.md)：使用指南（业务用户与管理员）
- [TESTING.md](TESTING.md)：测试指南
- [PRD.md](PRD.md)：经营分析 Agent 的业务数据模型与 API 契约

## 测试

```bash
.venv/bin/python -m pytest backend/tests -q
```

```bash
cd frontend && npm run lint && npm run build
```

后端 80 个测试用例覆盖：SQL 安全校验、CSV 导入、知识库单元、API 集成、认证与权限、Wiki 生命周期、推理循环（工具调用、SQL 自我修正、步数上限、引用校验、无模型降级）、记忆与上下文、模型 / 工具 / 技能管理（加密、注入、SSRF）。大模型调用使用脚本化的假模型，不消耗额度；需要数据库的用例在数据库不可用时自动跳过。

## 路线图

- 扫描件 PDF 的 OCR、Excel / PPT 导入
- 部门级空间与页面权限、企业 SSO（LDAP / OIDC）
- Rerank 模型提升检索精度
- MCP（Model Context Protocol）工具接入
- 多 Agent 协作与定时任务（周报自动生成）

## 许可证

[Apache License 2.0](LICENSE)
