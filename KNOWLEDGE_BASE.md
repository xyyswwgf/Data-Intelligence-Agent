# 企业知识库（RAG + Wiki）设计说明

本文记录知识库模块的设计决策，面向私有化部署的企业客户。

## 1. 目标

- 企业把制度、指标口径、业务手册、项目文档等资料（Markdown / TXT / PDF / Word）导入系统
- 所有文档统一转换为 **Markdown Wiki 页面**，形成可浏览、可编辑、可追溯版本的知识库
- 自动生成 **Wiki 索引**（目录树、标签索引、最近更新、双向链接），方便人整理和查找
- 基于知识库做 **RAG 问答**：回答必须引用来源页面，可点击跳转核对
- 经营分析 Agent 的「检索业务知识」步骤改为检索企业知识库，口径以客户自己的文档为准

## 2. 部署形态

私有化部署（每个客户一套 Docker Compose），单租户 + 用户/角色权限：

| 角色 | 权限 |
|------|------|
| `viewer` 只读成员 | 浏览 Wiki、搜索、知识问答、经营分析、SQL 查询 |
| `editor` 编辑 | viewer 全部 + 上传文档、新建/编辑 Wiki、导入 CSV |
| `admin` 管理员 | editor 全部 + 删除页面/数据表、用户管理、审计日志 |

首次启动时用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 环境变量初始化管理员（未设置时生成随机密码打印到日志）。

## 3. 架构

```text
上传文件 ──► 原件落盘（KB_STORAGE_DIR，sha256 去重）
          └► 转换为 Markdown（md/txt 直通，PDF: pypdf，Word: python-docx 保留标题/列表/表格）
              └► 创建 Wiki 页面（版本 1）
                  └► 按标题层级切块（~800 字，带重叠，保留标题路径）
                      └► 向量化（Embedding Provider）──► kb_chunks.embedding (pgvector)

提问 ──► 混合检索：向量相似度 (pgvector HNSW) + 关键词 (pg_trgm，支持中文子串)
          └► RRF 融合排序 ──► Top-K 片段
              └► LLM 生成带 [n] 引用的回答（未配置 LLM 时返回抽取式答案）
```

### 3.1 Embedding：可插拔、默认免费

统一走 **OpenAI 兼容的 `/v1/embeddings` 接口**，默认模型 `bge-m3`（1024 维，中文效果好，开源免费）：

| 方案 | 配置 | 费用 | 数据出网 |
|------|------|------|---------|
| 本地 Ollama（默认） | `EMBEDDING_BASE_URL=http://localhost:11434/v1`，`EMBEDDING_MODEL=bge-m3` | 免费 | 否 |
| 硅基流动云端 | `EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1`，`EMBEDDING_MODEL=BAAI/bge-m3`，`EMBEDDING_API_KEY=...` | 免费额度 | 是 |
| 关闭 | `EMBEDDING_PROVIDER=none` | — | 否 |

两种 bge-m3 输出同一向量空间，**云端 ↔ 本地切换无需重建索引**。
国内网络无法访问 Ollama 官方仓库时，可从 ModelScope 拉取同一模型的 GGUF 版本（`modelscope.cn/gpustack/bge-m3-GGUF`，约 437MB）后 `ollama cp` 为 `bge-m3`。

**相似度门槛**：bge-m3 实测无关问题的余弦相似度约 0.35–0.45，相关问题 ≥0.6。向量结果需同时满足
`≥ RAG_MIN_SIMILARITY`（默认 0.5）且不低于最佳结果 `RAG_RELATIVE_SIMILARITY`（默认 0.2）以内，避免把不相关的片段塞给 LLM。Embedding 服务不可用时自动降级为纯关键词检索，问答不中断；
恢复后可在管理页一键「重建索引」补齐向量。

### 3.2 数据模型（`backend/migrations/`）

| 表 | 说明 |
|----|------|
| `users` / `auth_sessions` | 用户（scrypt 密码哈希）与可吊销的登录会话（只存 token 的 SHA-256） |
| `audit_logs` | 登录、上传、编辑、删除、用户变更等操作审计 |
| `kb_documents` | 上传的原始文件：文件名、类型、大小、sha256、处理状态、错误信息、对应页面 |
| `wiki_pages` | Wiki 页面：slug、标题、父页面（目录树）、Markdown 正文、标签、版本号、软删除 |
| `wiki_page_versions` | 每次保存一个版本，可查看历史和回滚 |
| `wiki_links` | 页面中的 `[[slug]]` 链接，用于反向链接 |
| `kb_chunks` | 检索切块：标题路径、正文、`embedding vector(1024)`、trigram 索引 |
| `agent_runs` | 分析历史持久化（原先在内存中，重启丢失）；普通成员只能看到自己的记录 |

迁移由启动时的轻量迁移器按序执行（`schema_migrations` 记录版本），升级客户环境只需重启服务。

## 4. 安全与合规

- 所有业务接口需要登录（`Authorization: Bearer <token>`），按角色鉴权
- LLM / Agent 生成的 SQL 以数据库只读角色 `nl2sql_reader` 执行，系统表（账号、审计、知识库）对其不可见
- 密码 scrypt 哈希；会话 token 只存哈希、可过期、可吊销；连续登录失败限流
- 上传文件类型白名单 + 大小上限 + sha256 去重；原件不对外直接暴露路径
- Markdown 渲染在前端做 XSS 清洗
- 关键操作写审计日志，管理员可检索

## 5. 后续规划

- 扫描件 PDF 的 OCR、Excel/PPT 导入
- 部门级空间权限（页面 ACL）
- 接入企业 SSO（LDAP / OIDC）
- Rerank 模型（bge-reranker）提升检索精度
