# 智能助手：推理与记忆设计说明

## 1. 关于 DeepInfer 的结论

DeepSeek 开源的 DeepInfer 是 **GPU 推理服务引擎**（与 vLLM 同类：投机解码、Prefill 优化、PD 分离），
目前只支持 DeepSeek-V4.1-Flash，需要 NVIDIA Blackwell 或华为昇腾 950 集群。它解决的是「模型厂商如何部署模型」，
不是「应用如何调用模型完成推理任务」，因此不直接接入本平台。

本平台在应用层实现推理能力：**任何 API 模型（或自部署在 vLLM / SGLang / DeepInfer / Ollama 上的模型）
只要提供 Anthropic 或 OpenAI 兼容接口，就能驱动下面的推理循环。**

## 2. 推理架构（`backend/assistant/agent.py`）

```text
问题 → 组装上下文（指令 + 记忆 + 摘要 + 历史，按 token 预算）
     → 模型思考（原生 thinking / reasoning，或显式计划）
     → 调用工具 ──► 观察结果 ──► 继续思考 …（ReAct，最多 AGENT_MAX_STEPS 步）
     → 自检后作答（数字必须来自工具结果；引用校验，编造的 [[页面]] 会被去掉）
     → 落库 → 后台抽取长期记忆 → 按需压缩会话
```

| 机制 | 说明 |
|------|------|
| 统一 LLM 网关 | `llm_gateway.py`：Anthropic / OpenAI 两种协议，原生推理内容统一解析为 `reasoning` |
| 工具 | 检索知识库、列出数据表、读取表结构、执行只读 SQL、经营归因分析、CSV 表分析、写入记忆 |
| 自我修正 | 工具报错（SQL 列名写错等）原文回传给模型，由模型修正后重试；重复调用直接跳过 |
| 步数上限 | 达到上限后最后一轮不再提供工具，强制基于已有证据作答并说明缺口 |
| 安全边界不变 | SQL 仍经过 sql_guard + 只读角色 + READ ONLY 事务；工具结果超长时截断再进上下文 |
| 事件流 | SSE 实时推送 context / thinking / tool_call / tool_result / done，前端展示推理轨迹 |
| 无模型降级 | 未配置 LLM 时按规则路由到经营分析 / CSV 分析 / 知识检索，平台不中断 |

## 3. 记忆分层（借鉴 Letta / MemGPT 分层记忆、Mem0 自动抽取）

| 层级 | 存储 | 进入上下文的方式 |
|------|------|------------------|
| 工作记忆 | `chat_messages` 最近几轮原文 | 从最新往前按预算填充，至少保留上一轮 |
| 会话摘要 | `chat_sessions.summary` | 未压缩消息超过窗口 `CONTEXT_COMPACT_RATIO` 时，把较早消息合并进滚动摘要 |
| 核心记忆 | `user_memories`（pinned） | 每轮都带上（Letta 的 core memory） |
| 相关记忆 | `user_memories` 向量召回 | 按问题语义取 Top-K |
| 组织记忆 | 企业知识库 | 由模型按需调用 `search_knowledge` |

写入路径：用户说「记住：…」（不依赖模型）、模型调用 `remember`、每轮结束后后台由模型抽取（Mem0 式）。
相似度很高的记忆合并更新而不是重复新增；每人上限 500 条，超出淘汰最久未用的非置顶记忆。
用户在「我的记忆」页面可以查看、编辑、置顶、删除、清空，或整体关闭记忆；记忆只对本人可见。

## 4. 上下文预算（`backend/assistant/context.py`）

系统指令与工具定义、当前问题必须保留；核心记忆 ≤6%、相关记忆 ≤6%、会话摘要 ≤12%，剩余给最近对话。
每轮把各部分占用推送给前端，显示为上下文占用条。token 按「中文 1 字≈1 token、其他约 4 字符≈1 token」保守估算。

## 5. 模型切换（本地 ↔ 云端）

管理员在「系统管理 → 模型」中维护多个模型配置（`llm_profiles`），环境变量中的 LLM 配置作为兜底：

- 本地：Ollama / vLLM / SGLang 等 OpenAI 兼容服务；云端：DeepSeek、通义千问、Claude 等
- 一键预设常见服务，「获取模型列表」读取服务的 `/models` 接口
- 「测试连接」实测一次对话和一次工具调用，记录延迟与是否支持工具调用（不支持的模型在选择时会标注）
- API Key 以 Fernet 加密存库（密钥来自 `APP_SECRET_KEY`），接口只返回掩码，编辑时留空不修改
- 一个默认模型用于新对话；用户在输入框下方按对话切换，同一会话后续轮次沿用所选模型；
  模型被停用或删除时自动回退到默认模型，对话不中断
- 记忆提炼、会话摘要等后台任务使用与本轮相同的模型

## 6. 工具与技能管理

**工具**（「系统管理 → 工具」，`agent_tools`）

| 类型 | 说明 |
|------|------|
| 内置工具 | 知识库检索、表结构、只读 SQL、经营归因、CSV 分析、记忆、加载技能；可停用、可设置最低使用角色 |
| SQL 模板 | 把常用口径固化成参数化查询（`:month` 引用参数）。保存时经 sql_guard 校验，参数由数据库驱动绑定（注入无效），执行仍走只读角色 |
| HTTP 接口 | 接入 CRM、工单、库存等系统。URL 由管理员固定，模型只能填参数（路径参数 URL 编码）；凭证头加密存储；默认禁止访问内网地址，需要时用 `TOOL_HTTP_ALLOWED_HOSTS` 放行；响应可按 `response_path` 只取有用字段 |

参数用简化的 JSON Schema（文本 / 整数 / 数字 / 是否，可设可选值和必填），模型给出的参数在执行前校验并转换类型，
错误信息回传给模型修正。每个工具都可以在管理页填参数直接试运行。出于安全考虑不支持执行任意代码。

**技能**（「系统管理 → 技能」，`agent_skills`，借鉴 Anthropic Agent Skills 的渐进式加载）

- 技能 = 标识 + 适用场景 + 详细指引（Markdown）+ 推荐工具
- 系统提示只列出技能名称和适用场景；模型判断需要时调用 `use_skill` 加载完整指引，不占用多余上下文
- 用户也可在输入框的「技能」菜单手动指定，此时指引直接注入本轮
- 内置示例：经营周报、数据质量巡检

## 7. 配置

| 变量 | 说明 |
|------|------|
| `LLM_PROVIDER` | `anthropic`（默认）或 `openai_compatible` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容接口地址、Key、模型名 |
| `LLM_CONTEXT_WINDOW` | 模型上下文窗口，用于预算分配 |
| `LLM_THINKING_BUDGET` | Anthropic 协议的 thinking 预算（0 关闭） |
| `LLM_REASONING_EFFORT` / `LLM_NO_REASONING_EFFORT` | OpenAI 协议推理强度；后台任务关闭推理用的值（Ollama/Qwen 设为 `none`） |
| `AGENT_MAX_STEPS` | 单轮最多工具调用步数，默认 8 |
| `MEMORY_ENABLED` / `MEMORY_RECALL_K` | 全局记忆开关、每轮召回条数 |
| `CONTEXT_COMPACT_RATIO` / `CONTEXT_KEEP_RECENT` | 会话压缩阈值、压缩时保留的最近消息数 |

本地验证用 Ollama 的 `qwen3.5:4b`（支持工具调用和推理）：

```bash
LLM_PROVIDER=openai_compatible LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen3.5:4b-mlx LLM_NO_REASONING_EFFORT=none
```

小模型能跑通完整流程，但多步推理时偶尔会混淆数字（例如把 6 月的环比写到 5 月下），正式使用建议接入 DeepSeek、Qwen-Max 等大模型。
