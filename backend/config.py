"""
应用配置 — 所有敏感信息从系统环境变量读取
"""
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()

# ── PostgreSQL 数据库配置 ──
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "15432"))
DB_USER = os.getenv("DB_USER", "data_agent")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Simple@123")  # 仅本地开发默认值，生产环境必须覆盖
DB_NAME = os.getenv("DB_NAME", "data_agent_db")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql+psycopg://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
)

# 内部表（账号、审计、知识库、分析存档等）不暴露给 LLM 和前端表列表
SYSTEM_TABLE_PREFIXES = tuple(
    p.strip()
    for p in os.getenv("SYSTEM_TABLE_PREFIXES", "knowledge_,analysis_,table_profiles,wiki_,kb_,auth_,audit_").split(",")
    if p.strip()
)
SYSTEM_TABLES = {
    "users", "agent_runs", "schema_migrations", "chat_sessions", "chat_messages", "user_memories",
    "llm_profiles", "agent_tools", "agent_skills",
}
# LLM / Agent 生成的 SQL 以该数据库角色执行：只对业务表有 SELECT 权限，读不到账号、审计、知识库等系统表
SQL_READER_ROLE = os.getenv("SQL_READER_ROLE", "nl2sql_reader")

# ── 只读查询防护（纵深防御第三层） ──
QUERY_MAX_ROWS = int(os.getenv("QUERY_MAX_ROWS", "2000"))
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "15000"))

# ── LLM / Anthropic-compatible 配置 ──
ANTHROPIC_API_KEY = (
    os.getenv("ANTHROPIC_API_KEY")
    or os.getenv("ANTHROPIC_AUTH_TOKEN")
    or os.getenv("DEEPSEEK_API_KEY")
    or ""
)
ANTHROPIC_BASE_URL = os.getenv(
    "ANTHROPIC_BASE_URL",
    "https://api.deepseek.com/anthropic",
)
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro")
# 协议：anthropic（DeepSeek / Claude 的 Anthropic 兼容接口）或 openai_compatible（DeepSeek / Qwen / vLLM / Ollama 等）
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")  # openai_compatible 时使用，如 https://api.deepseek.com/v1
LLM_API_KEY = os.getenv("LLM_API_KEY", "") or ANTHROPIC_API_KEY
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048"))
# 模型上下文窗口（token），上下文管理按它分配预算
LLM_CONTEXT_WINDOW = int(os.getenv("LLM_CONTEXT_WINDOW", "64000"))
# 原生推理（thinking）：Anthropic 协议按预算开启；OpenAI 协议透传 reasoning_effort（模型不支持时自动忽略）
LLM_THINKING_BUDGET = int(os.getenv("LLM_THINKING_BUDGET", "0"))
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "")  # low | medium | high，留空不传
# 后台轻量任务（记忆抽取、会话摘要、SQL 生成）关闭推理时传给 reasoning_effort 的值。
# Ollama / Qwen 等推理模型设为 none 可避免思考过程耗尽 max_tokens；官方 OpenAI / DeepSeek 留空即可
LLM_NO_REASONING_EFFORT = os.getenv("LLM_NO_REASONING_EFFORT", "")

# ── 智能助手（推理 Agent）与记忆 ──
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "8"))
MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "true").lower() in {"1", "true", "yes"}
MEMORY_RECALL_K = int(os.getenv("MEMORY_RECALL_K", "6"))
# 会话未压缩消息超过上下文窗口的该比例时，把较早消息压缩进滚动摘要
CONTEXT_COMPACT_RATIO = float(os.getenv("CONTEXT_COMPACT_RATIO", "0.5"))
CONTEXT_KEEP_RECENT = int(os.getenv("CONTEXT_KEEP_RECENT", "6"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
# SQL 执行失败后，把错误信息回传给 LLM 自我修正的最大次数
LLM_SQL_REPAIR_ATTEMPTS = int(os.getenv("LLM_SQL_REPAIR_ATTEMPTS", "1"))

# ── CSV 导入配置 ──
CSV_MAX_FILE_SIZE_MB = int(os.getenv("CSV_MAX_FILE_SIZE_MB", "10"))
CSV_MAX_ROWS = int(os.getenv("CSV_MAX_ROWS", "100000"))
CSV_BATCH_SIZE = int(os.getenv("CSV_BATCH_SIZE", "500"))

# ── 认证 ──
AUTH_SESSION_TTL_HOURS = int(os.getenv("AUTH_SESSION_TTL_HOURS", "12"))
AUTH_MAX_FAILED_LOGINS = int(os.getenv("AUTH_MAX_FAILED_LOGINS", "5"))
AUTH_LOCK_MINUTES = int(os.getenv("AUTH_LOCK_MINUTES", "15"))
# 首次启动（users 表为空）时创建的管理员；未设置密码则生成随机密码并打印到日志
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# ── 知识库 / RAG ──
KB_STORAGE_DIR = os.getenv("KB_STORAGE_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "kb_files"))
KB_MAX_FILE_MB = int(os.getenv("KB_MAX_FILE_MB", "30"))
KB_CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", "800"))
KB_CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", "120"))
# 测试或单机调试时可设为 false，让索引同步执行
KB_INDEX_ASYNC = os.getenv("KB_INDEX_ASYNC", "true").lower() in {"1", "true", "yes"}
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))
# 向量检索的相似度门槛：低于绝对值、或比最相关结果低太多的片段视为无关（bge-m3 实测无关问题约 0.35~0.45）
RAG_MIN_SIMILARITY = float(os.getenv("RAG_MIN_SIMILARITY", "0.5"))
RAG_RELATIVE_SIMILARITY = float(os.getenv("RAG_RELATIVE_SIMILARITY", "0.2"))

# Embedding：OpenAI 兼容的 /v1/embeddings 接口。默认本地 Ollama 的 bge-m3（免费、数据不出网）；
# 切换到硅基流动云端 BAAI/bge-m3 只需改 BASE_URL / MODEL / API_KEY，向量空间一致，无需重建索引
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai_compatible")  # openai_compatible | none
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434/v1").rstrip("/")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))
EMBEDDING_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "60"))

# ── 密钥 / 自定义工具 ──
# 用于加密数据库中保存的模型 API Key、工具凭证。生产环境务必设置并妥善保管（丢失后已保存的 Key 需重新填写）；
# 未设置时自动生成并保存在数据目录下的 .secret_key 文件
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "")
APP_DATA_DIR = os.getenv("APP_DATA_DIR", os.path.dirname(KB_STORAGE_DIR))
# HTTP 工具允许访问的内网主机（逗号分隔）。默认禁止访问内网/本机地址，防止 SSRF
TOOL_HTTP_ALLOWED_HOSTS = {h.strip().lower() for h in os.getenv("TOOL_HTTP_ALLOWED_HOSTS", "").split(",") if h.strip()}
TOOL_HTTP_TIMEOUT_SECONDS = float(os.getenv("TOOL_HTTP_TIMEOUT_SECONDS", "15"))

# ── Python 分析沙箱（智能助手的 python_analysis 工具）──
PYTHON_SANDBOX_ENABLED = os.getenv("PYTHON_SANDBOX_ENABLED", "true").lower() in {"1", "true", "yes"}
PYTHON_SANDBOX_TIMEOUT_SECONDS = int(os.getenv("PYTHON_SANDBOX_TIMEOUT_SECONDS", "20"))
PYTHON_SANDBOX_MEMORY_MB = int(os.getenv("PYTHON_SANDBOX_MEMORY_MB", "1024"))
PYTHON_SANDBOX_MAX_ROWS = int(os.getenv("PYTHON_SANDBOX_MAX_ROWS", "50000"))

# ── Web 配置 ──
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
ANALYSIS_HISTORY_LIMIT = int(os.getenv("ANALYSIS_HISTORY_LIMIT", "100"))
