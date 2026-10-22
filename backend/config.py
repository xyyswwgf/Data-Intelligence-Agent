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

# 内部表（知识库、历史分析存档等）不暴露给 LLM 和前端表列表
SYSTEM_TABLE_PREFIXES = tuple(
    p.strip() for p in os.getenv("SYSTEM_TABLE_PREFIXES", "knowledge_,analysis_,table_profiles").split(",") if p.strip()
)

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
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
# SQL 执行失败后，把错误信息回传给 LLM 自我修正的最大次数
LLM_SQL_REPAIR_ATTEMPTS = int(os.getenv("LLM_SQL_REPAIR_ATTEMPTS", "1"))

# ── CSV 导入配置 ──
CSV_MAX_FILE_SIZE_MB = int(os.getenv("CSV_MAX_FILE_SIZE_MB", "10"))
CSV_MAX_ROWS = int(os.getenv("CSV_MAX_ROWS", "100000"))
CSV_BATCH_SIZE = int(os.getenv("CSV_BATCH_SIZE", "500"))

# ── Web 配置 ──
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
ANALYSIS_HISTORY_LIMIT = int(os.getenv("ANALYSIS_HISTORY_LIMIT", "100"))
