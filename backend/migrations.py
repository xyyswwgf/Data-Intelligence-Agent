"""
轻量数据库迁移器 — 启动时按版本顺序执行，已执行的版本记录在 schema_migrations

客户环境升级只需替换镜像并重启服务；迁移在 PostgreSQL advisory lock 下执行，
多个后端实例同时启动也不会重复执行。
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.config import EMBEDDING_DIM, SQL_READER_ROLE
from backend.database import engine, quote_identifier

logger = logging.getLogger("nl2sql.migrations")

_LOCK_KEY = 74_210_031  # 任意常量，用于 pg_advisory_xact_lock


def _m001_core_schema(conn: Connection) -> None:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username VARCHAR(64) NOT NULL UNIQUE,
            display_name VARCHAR(100) NOT NULL,
            password_hash TEXT NOT NULL,
            role VARCHAR(16) NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            failed_logins INT NOT NULL DEFAULT 0,
            locked_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        );
        COMMENT ON TABLE users IS '系统用户（系统表，不对 LLM 开放）';

        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash CHAR(64) PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ip VARCHAR(64),
            user_agent VARCHAR(300)
        );
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);

        CREATE TABLE IF NOT EXISTS audit_logs (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            username VARCHAR(64),
            action VARCHAR(64) NOT NULL,
            target_type VARCHAR(32),
            target_id TEXT,
            detail JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ip VARCHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);

        CREATE TABLE IF NOT EXISTS kb_documents (
            id BIGSERIAL PRIMARY KEY,
            filename VARCHAR(300) NOT NULL,
            file_type VARCHAR(16) NOT NULL,
            size_bytes BIGINT NOT NULL,
            sha256 CHAR(64) NOT NULL UNIQUE,
            storage_path TEXT NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'ready', 'failed')),
            error TEXT,
            page_id BIGINT,
            uploaded_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS wiki_pages (
            id BIGSERIAL PRIMARY KEY,
            slug VARCHAR(200) NOT NULL UNIQUE,
            title VARCHAR(300) NOT NULL,
            parent_id BIGINT REFERENCES wiki_pages(id) ON DELETE SET NULL,
            content_md TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            tags TEXT[] NOT NULL DEFAULT '{{}}',
            source_document_id BIGINT REFERENCES kb_documents(id) ON DELETE SET NULL,
            version INT NOT NULL DEFAULT 1,
            sort_order INT NOT NULL DEFAULT 0,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            index_status VARCHAR(16) NOT NULL DEFAULT 'pending'
                CHECK (index_status IN ('pending', 'indexing', 'ready', 'partial', 'failed')),
            index_error TEXT,
            created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_wiki_pages_parent ON wiki_pages(parent_id);
        CREATE INDEX IF NOT EXISTS idx_wiki_pages_tags ON wiki_pages USING gin(tags);
        CREATE INDEX IF NOT EXISTS idx_wiki_pages_title_trgm ON wiki_pages USING gin(title gin_trgm_ops);

        ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_page_fk;
        ALTER TABLE kb_documents
            ADD CONSTRAINT kb_documents_page_fk FOREIGN KEY (page_id) REFERENCES wiki_pages(id) ON DELETE SET NULL;

        CREATE TABLE IF NOT EXISTS wiki_page_versions (
            page_id BIGINT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
            version INT NOT NULL,
            title VARCHAR(300) NOT NULL,
            content_md TEXT NOT NULL,
            tags TEXT[] NOT NULL DEFAULT '{{}}',
            change_note VARCHAR(300),
            edited_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            edited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (page_id, version)
        );

        CREATE TABLE IF NOT EXISTS wiki_links (
            from_page_id BIGINT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
            to_slug VARCHAR(200) NOT NULL,
            PRIMARY KEY (from_page_id, to_slug)
        );
        CREATE INDEX IF NOT EXISTS idx_wiki_links_to ON wiki_links(to_slug);

        CREATE TABLE IF NOT EXISTS kb_chunks (
            id BIGSERIAL PRIMARY KEY,
            page_id BIGINT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
            chunk_index INT NOT NULL,
            heading_path TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            char_count INT NOT NULL,
            embedding vector({int(EMBEDDING_DIM)}),
            embedding_model VARCHAR(100),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_page ON kb_chunks(page_id);
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_content_trgm ON kb_chunks USING gin(content gin_trgm_ops);
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding ON kb_chunks USING hnsw (embedding vector_cosine_ops);

        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id UUID PRIMARY KEY,
            question TEXT NOT NULL,
            table_name VARCHAR(200),
            status VARCHAR(16) NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            result JSONB NOT NULL,
            created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_by, created_at DESC);
    """))


def _m002_reader_role(conn: Connection) -> None:
    """
    LLM / Agent SQL 使用的只读角色：只能 SELECT 业务表。

    以后新建的表（CSV 导入表）通过 DEFAULT PRIVILEGES 自动获得 SELECT；
    系统表在这里显式 REVOKE，后续新增系统表的迁移也必须 REVOKE。
    """
    if not SQL_READER_ROLE:
        return
    role = quote_identifier(SQL_READER_ROLE)
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": SQL_READER_ROLE}).first()
    if not exists:
        conn.execute(text(f"CREATE ROLE {role} NOLOGIN"))
    conn.execute(text(f"GRANT {role} TO CURRENT_USER"))
    conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}"))
    conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role}"))
    for table in _system_tables(conn):
        conn.execute(text(f"REVOKE ALL ON {quote_identifier(table)} FROM {role}"))


def _m003_seed_wiki(conn: Connection) -> None:
    """把演示数据里的 knowledge_chunks 转成 Wiki 页面，作为知识库的初始内容。"""
    has_legacy = conn.execute(text("SELECT to_regclass('public.knowledge_chunks') IS NOT NULL")).scalar()
    has_pages = conn.execute(text("SELECT EXISTS (SELECT 1 FROM wiki_pages)")).scalar()
    if not has_legacy or has_pages:
        return
    rows = conn.execute(text("SELECT source, content FROM knowledge_chunks ORDER BY chunk_id")).all()
    if not rows:
        return
    titles = {
        "metrics.md": ("指标口径", "metrics", ["指标", "口径"]),
        "business_rules.md": ("业务规则", "business-rules", ["规则"]),
        "analysis_playbooks.md": ("分析手册", "analysis-playbooks", ["方法论"]),
    }
    root_id = conn.execute(
        text("""
            INSERT INTO wiki_pages (slug, title, content_md, summary, tags)
            VALUES ('home', '知识库首页', :content, '企业知识库入口', ARRAY['导航'])
            RETURNING id
        """),
        {"content": "# 知识库首页\n\n欢迎使用企业知识库。\n\n- [[metrics]] 指标口径\n- [[business-rules]] 业务规则\n- [[analysis-playbooks]] 分析手册\n"},
    ).scalar()
    grouped: dict[str, list[str]] = {}
    for source, content in rows:
        grouped.setdefault(source, []).append(content)
    for order, (source, contents) in enumerate(grouped.items()):
        title, slug, tags = titles.get(source, (source.rsplit(".", 1)[0], source.rsplit(".", 1)[0], []))
        body = f"# {title}\n\n" + "\n\n".join(f"- {c}" for c in contents) + "\n\n返回 [[home]]\n"
        conn.execute(
            text("""
                INSERT INTO wiki_pages (slug, title, parent_id, content_md, summary, tags, sort_order)
                VALUES (:slug, :title, :parent, :content, :summary, :tags, :order)
            """),
            {"slug": slug, "title": title, "parent": root_id, "content": body,
             "summary": contents[0][:120], "tags": tags, "order": order},
        )
    conn.execute(text("""
        INSERT INTO wiki_page_versions (page_id, version, title, content_md, tags, change_note)
        SELECT id, 1, title, content_md, tags, '初始化：从演示知识库导入' FROM wiki_pages
    """))
    conn.execute(text("""
        INSERT INTO wiki_links (from_page_id, to_slug)
        SELECT p.id, m[1] FROM wiki_pages p, regexp_matches(p.content_md, '\\[\\[([^\\]|]+)', 'g') AS m
        ON CONFLICT DO NOTHING
    """))


def _m004_conversations_and_memory(conn: Connection) -> None:
    """对话会话、消息（含推理轨迹）与用户长期记忆。"""
    conn.execute(text(f"""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT TRUE;

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(200) NOT NULL DEFAULT '新对话',
            summary TEXT NOT NULL DEFAULT '',
            summary_upto INT NOT NULL DEFAULT 0,
            message_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, updated_at DESC);
        COMMENT ON COLUMN chat_sessions.summary IS '滚动摘要：较早的消息被压缩到这里，节省上下文';
        COMMENT ON COLUMN chat_sessions.summary_upto IS '已被摘要覆盖的最大消息序号';

        CREATE TABLE IF NOT EXISTS chat_messages (
            id BIGSERIAL PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            seq INT NOT NULL,
            role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
            mode VARCHAR(16) NOT NULL DEFAULT 'assistant',
            content TEXT NOT NULL DEFAULT '',
            payload JSONB,
            trace JSONB,
            token_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (session_id, seq)
        );

        CREATE TABLE IF NOT EXISTS user_memories (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            category VARCHAR(16) NOT NULL DEFAULT 'fact' CHECK (category IN ('preference', 'fact', 'goal', 'instruction')),
            pinned BOOLEAN NOT NULL DEFAULT FALSE,
            source VARCHAR(16) NOT NULL DEFAULT 'auto' CHECK (source IN ('auto', 'explicit', 'manual')),
            source_session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
            embedding vector({int(EMBEDDING_DIM)}),
            use_count INT NOT NULL DEFAULT 0,
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id);
        CREATE INDEX IF NOT EXISTS idx_user_memories_trgm ON user_memories USING gin(content gin_trgm_ops);
        CREATE INDEX IF NOT EXISTS idx_user_memories_embedding ON user_memories USING hnsw (embedding vector_cosine_ops);
    """))
    if SQL_READER_ROLE and conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": SQL_READER_ROLE}).first():
        for table in ("chat_sessions", "chat_messages", "user_memories"):
            conn.execute(text(f"REVOKE ALL ON {table} FROM {quote_identifier(SQL_READER_ROLE)}"))


_BUILTIN_TOOLS = [
    ("search_knowledge", "检索知识库", "检索企业知识库（制度、口径、规则、手册）"),
    ("list_tables", "查看数据表", "列出可查询的业务数据表"),
    ("describe_tables", "读取表结构", "查看数据表的字段定义"),
    ("run_sql", "执行 SQL", "在只读环境执行 SELECT 查询"),
    ("business_analysis", "门店经营分析", "各门店销售、毛利、任务达成、产品线与库龄分析"),
    ("analyze_imported_table", "分析导入表", "对 CSV 导入表做趋势、分组、缺失、异常分析"),
    ("remember", "写入记忆", "把用户的长期信息存入记忆"),
    ("use_skill", "加载技能", "按需加载技能的详细指引"),
]

_SEED_SKILLS = [
    (
        "weekly-report",
        "经营周报",
        "用户要写周报、月报、经营汇报材料，或要求「按汇报格式输出」时使用。",
        """# 经营周报写作规范

1. 先调用 business_analysis 获取各门店销售额、毛利、任务达成和产品线结构；需要补充数字时再用 run_sql。
2. 输出结构固定为：
   - **一句话结论**（不超过 40 字，包含毛利达成率等最关键的数字）
   - **门店指标表**：门店 / 销售额 / 毛利 / 毛利率 / 手机台数 / 毛利目标达成率，金额以万元为单位保留两位小数
   - **三条主要原因**：每条都要有数据支撑（产品线、店员、库龄、收款方式等）
   - **下周行动建议**：最多三条，写清负责门店或店员
3. 口径与知识库保持一致（销售额、毛利、手机台数、回收业务）；产品政策、考核规则先调用 search_knowledge。
4. 不写空话，每个判断都要对应到数字。""",
        ["business_analysis", "run_sql", "search_knowledge"],
    ),
    (
        "data-quality-check",
        "数据质量巡检",
        "用户关心数据是否完整、可信，或问「这个表有没有问题 / 能不能用」时使用。",
        """# 数据质量巡检流程

1. 用 list_tables / describe_tables 确认目标表和字段。
2. 依次检查并用 run_sql 给出证据：
   - 行数与时间覆盖范围（最早、最晚日期，是否有断档的日期）
   - 关键字段缺失率
   - 主键/业务键是否重复
   - 数值字段的负数、零值、极端值
3. 导入的 CSV 表可直接调用 analyze_imported_table 做缺失和异常初筛。
4. 结论按「可以直接使用 / 需要注意 / 不建议使用」三档给出，并列出需要业务方确认的问题。""",
        ["list_tables", "describe_tables", "run_sql", "analyze_imported_table"],
    ),
]


def _m005_models_tools_skills(conn: Connection) -> None:
    """模型配置（本地 / 云端切换）、可管理的工具、技能。"""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS llm_profiles (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            kind VARCHAR(16) NOT NULL DEFAULT 'cloud' CHECK (kind IN ('local', 'cloud')),
            provider VARCHAR(32) NOT NULL CHECK (provider IN ('anthropic', 'openai_compatible')),
            base_url TEXT NOT NULL,
            api_key_encrypted TEXT,
            model VARCHAR(200) NOT NULL,
            context_window INT NOT NULL DEFAULT 64000,
            max_output_tokens INT NOT NULL DEFAULT 2048,
            thinking_budget INT NOT NULL DEFAULT 0,
            reasoning_effort VARCHAR(16) NOT NULL DEFAULT '',
            no_reasoning_effort VARCHAR(16) NOT NULL DEFAULT '',
            timeout_seconds INT NOT NULL DEFAULT 60,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            last_test JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_profiles_default ON llm_profiles (is_default) WHERE is_default;

        CREATE TABLE IF NOT EXISTS agent_tools (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(64) NOT NULL UNIQUE,
            display_name VARCHAR(100) NOT NULL,
            description TEXT NOT NULL,
            kind VARCHAR(16) NOT NULL CHECK (kind IN ('builtin', 'http', 'sql')),
            parameters JSONB NOT NULL DEFAULT '{"type": "object", "properties": {}}'::jsonb,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            secrets_encrypted TEXT,
            min_role VARCHAR(16) NOT NULL DEFAULT 'viewer' CHECK (min_role IN ('viewer', 'editor', 'admin')),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS agent_skills (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(64) NOT NULL UNIQUE,
            title VARCHAR(100) NOT NULL,
            description TEXT NOT NULL,
            instructions TEXT NOT NULL,
            tools TEXT[] NOT NULL DEFAULT '{}',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS model_profile_id BIGINT REFERENCES llm_profiles(id) ON DELETE SET NULL;
    """))
    for name, display, desc in _BUILTIN_TOOLS:
        conn.execute(text("""
            INSERT INTO agent_tools (name, display_name, description, kind) VALUES (:n, :d, :desc, 'builtin')
            ON CONFLICT (name) DO NOTHING
        """), {"n": name, "d": display, "desc": desc})
    for name, title, desc, instructions, tools in _SEED_SKILLS:
        conn.execute(text("""
            INSERT INTO agent_skills (name, title, description, instructions, tools) VALUES (:n, :t, :d, :i, :tools)
            ON CONFLICT (name) DO NOTHING
        """), {"n": name, "t": title, "d": desc, "i": instructions, "tools": tools})
    if SQL_READER_ROLE and conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": SQL_READER_ROLE}).first():
        for table in ("llm_profiles", "agent_tools", "agent_skills"):
            conn.execute(text(f"REVOKE ALL ON {table} FROM {quote_identifier(SQL_READER_ROLE)}"))


def _m006_python_analysis_tool(conn: Connection) -> None:
    """注册 Python 分析沙箱工具；执行代码风险高于只读 SQL，默认仅编辑及以上角色可用，管理员可调整。"""
    conn.execute(text("""
        INSERT INTO agent_tools (name, display_name, description, kind, min_role)
        VALUES ('python_analysis', 'Python 分析', '在隔离沙箱中用 pandas 处理只读 SQL 取出的数据', 'builtin', 'editor')
        ON CONFLICT (name) DO NOTHING
    """))


def _m007_store_schema(conn: Connection) -> None:
    """门店经营数据表（替换旧版电商演示数据），并把内置工具 / 技能的说明改为门店场景。"""
    from backend.store_data.schema import create_schema

    create_schema(conn)
    conn.execute(text("""
        UPDATE agent_tools SET display_name = '门店经营分析',
               description = '各门店销售、毛利、任务达成、产品线与库龄分析'
        WHERE name = 'business_analysis' AND display_name = '经营归因分析'
    """))
    conn.execute(
        text("UPDATE agent_skills SET instructions = :new WHERE name = 'weekly-report' AND instructions = :old"),
        {"new": _SEED_SKILLS[0][3], "old": _DEMO_WEEKLY_REPORT},
    )


# 007 之前内置「经营周报」技能的指引（电商演示口径）；管理员没改过时替换为门店口径
_DEMO_WEEKLY_REPORT = """# 经营周报写作规范

1. 先调用 business_analysis 获取核心指标和归因结论；需要补充数字时再用 run_sql。
2. 输出结构固定为：
   - **一句话结论**（不超过 40 字，包含最关键的变化幅度）
   - **核心指标表**：指标 / 本期 / 上期 / 环比，金额以万元为单位保留两位小数
   - **三条主要原因**：每条都要有数据支撑
   - **下周行动建议**：最多三条，写清负责方向
3. 口径与知识库保持一致（GMV、净销售额、毛利率）；不确定的口径先调用 search_knowledge。
4. 不写空话，每个判断都要对应到数字。"""


def _m008_store_comments(conn: Connection) -> None:
    """手机台数改为按商品名称判断：更新表注释（智能助手据此写 SQL），已导入的数据按新规则重算 is_phone。"""
    from backend.store_data.schema import DDL

    conn.execute(text(DDL))
    conn.execute(text("""
        UPDATE fact_store_sales
        SET is_phone = (product_name LIKE '%全网通%' AND product_name NOT LIKE '%平板%' AND product_name NOT LIKE '%二手%'
                        AND product_type IS DISTINCT FROM '回收'
                        AND COALESCE(product_status, '') NOT IN ('系统已调总库', '系统已做批发')
                        AND COALESCE(sale_price, 0) > 0)
    """))


def _m009_drop_non_retail(conn: Connection) -> None:
    """调回总库、批发不是门店零售：删除已导入的这些行，销售额、毛利、台数都不再包含（导入程序同样跳过）。"""
    from backend.store_data.schema import DDL

    conn.execute(text(DDL))
    conn.execute(text("DELETE FROM fact_store_sales WHERE product_status IN ('系统已调总库', '系统已做批发')"))


def _m010_mask_remarks(conn: Connection) -> None:
    """已导入的备注中可能有顾客手机号、身份证号：按导入时同样的规则打码。"""
    from backend.store_data.parsers import mask_personal

    for table, key in (("fact_store_sales", "sale_id"), ("fact_payments", "payment_id"), ("fact_purchase_in", "in_id")):
        if not conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar():
            continue
        rows = conn.execute(text(f"SELECT {key}, remark FROM {table} WHERE remark ~ '[0-9]{{11}}'")).all()
        changed = [{"id": rid, "remark": mask_personal(remark)} for rid, remark in rows if mask_personal(remark) != remark]
        if changed:
            conn.execute(text(f"UPDATE {table} SET remark = :remark WHERE {key} = :id"), changed)


# 初始方案（管理员可在「设置 → 方案与计费」中修改）；价格单位为分，额度单位为点数
_SEED_PLANS = [
    ("free", "免费版", "体验智能分析与知识库", 0, 2000, True, 0,
     ["每月 2,000 点数", "智能分析、知识库问答", "门店经营分析"]),
    ("pro", "专业版", "个人日常分析", 4900, 30000, False, 10,
     ["每月 30,000 点数", "全部模型与技能", "额度用完可用余额继续"]),
    ("team", "团队版", "门店和部门共用", 19900, 150000, False, 20,
     ["每月 150,000 点数", "全部模型、技能与工具", "优先使用本地大模型"]),
]


def _m011_billing_and_market(conn: Connection) -> None:
    """使用量计量、方案（套餐）+ 余额计费、账单流水，以及用户在「发现」中启用 / 停用模型、技能、工具。"""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS billing_plans (
            id BIGSERIAL PRIMARY KEY,
            code VARCHAR(32) NOT NULL UNIQUE,
            name VARCHAR(50) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price_cents INT NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
            monthly_credits NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (monthly_credits >= 0),
            features JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_plans_default ON billing_plans (is_default) WHERE is_default;

        CREATE TABLE IF NOT EXISTS user_subscriptions (
            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            plan_id BIGINT NOT NULL REFERENCES billing_plans(id),
            period_start TIMESTAMPTZ NOT NULL,
            period_end TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ,
            used_credits NUMERIC(14, 4) NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS user_wallets (
            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            balance NUMERIC(14, 4) NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS usage_records (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id VARCHAR(64),
            mode VARCHAR(16),
            profile_id BIGINT REFERENCES llm_profiles(id) ON DELETE SET NULL,
            model VARCHAR(200),
            input_tokens INT NOT NULL DEFAULT 0,
            output_tokens INT NOT NULL DEFAULT 0,
            credits NUMERIC(14, 4) NOT NULL DEFAULT 0,
            plan_credits NUMERIC(14, 4) NOT NULL DEFAULT 0,
            balance_credits NUMERIC(14, 4) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_usage_user_time ON usage_records (user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_records (created_at DESC);

        CREATE TABLE IF NOT EXISTS billing_transactions (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind VARCHAR(16) NOT NULL CHECK (kind IN ('subscribe', 'recharge', 'adjust', 'refund')),
            plan_id BIGINT REFERENCES billing_plans(id) ON DELETE SET NULL,
            credits NUMERIC(14, 4) NOT NULL DEFAULT 0,
            amount_cents INT NOT NULL DEFAULT 0,
            channel VARCHAR(16) NOT NULL DEFAULT 'manual' CHECK (channel IN ('manual', 'wechat', 'alipay')),
            status VARCHAR(16) NOT NULL DEFAULT 'paid' CHECK (status IN ('pending', 'paid', 'failed', 'refunded')),
            external_no VARCHAR(64),
            note TEXT,
            operator_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_billing_tx_user ON billing_transactions (user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS user_market_prefs (
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind VARCHAR(16) NOT NULL CHECK (kind IN ('model', 'skill', 'tool')),
            item_key VARCHAR(100) NOT NULL,
            enabled BOOLEAN NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, kind, item_key)
        );

        ALTER TABLE llm_profiles ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
        ALTER TABLE llm_profiles ADD COLUMN IF NOT EXISTS price_input NUMERIC(10, 4);
        ALTER TABLE llm_profiles ADD COLUMN IF NOT EXISTS price_output NUMERIC(10, 4);
    """))
    for code, name, desc, price, credits, default, order, features in _SEED_PLANS:
        conn.execute(text("""
            INSERT INTO billing_plans (code, name, description, price_cents, monthly_credits, features, is_default, sort_order)
            VALUES (:c, :n, :d, :p, :cr, CAST(:f AS jsonb), :def, :o) ON CONFLICT (code) DO NOTHING
        """), {"c": code, "n": name, "d": desc, "p": price, "cr": credits, "f": json.dumps(features, ensure_ascii=False),
               "def": default, "o": order})


def _m012_orgs_spaces_settings(conn: Connection) -> None:
    """
    企业（组织）与知识空间：每个企业一个共享的企业空间，每个成员一个只有自己可见的个人空间；
    知识库页面和文档都归属一个空间。现有知识库全部归入默认企业的企业空间。
    另加系统设置表（当前行业包等）。
    """
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key VARCHAR(64) PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS organizations (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """))
    org_id = conn.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
    if org_id is None:
        org_id = conn.execute(text("INSERT INTO organizations (name) VALUES ('我的企业') RETURNING id")).scalar()
    conn.execute(text(f"""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS org_id BIGINT REFERENCES organizations(id);
        UPDATE users SET org_id = {int(org_id)} WHERE org_id IS NULL;
        ALTER TABLE users ALTER COLUMN org_id SET DEFAULT {int(org_id)};
        ALTER TABLE users ALTER COLUMN org_id SET NOT NULL;

        CREATE TABLE IF NOT EXISTS kb_spaces (
            id BIGSERIAL PRIMARY KEY,
            org_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            kind VARCHAR(16) NOT NULL CHECK (kind IN ('org', 'personal', 'team')),
            name VARCHAR(100) NOT NULL,
            owner_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((kind = 'personal') = (owner_id IS NOT NULL))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_spaces_personal ON kb_spaces (owner_id) WHERE kind = 'personal';
        CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_spaces_org ON kb_spaces (org_id) WHERE kind = 'org';
    """))
    space_id = conn.execute(text("""
        INSERT INTO kb_spaces (org_id, kind, name) VALUES (:o, 'org', '企业知识库')
        ON CONFLICT (org_id) WHERE kind = 'org' DO UPDATE SET name = kb_spaces.name RETURNING id
    """), {"o": org_id}).scalar()
    conn.execute(text(f"""
        ALTER TABLE wiki_pages ADD COLUMN IF NOT EXISTS space_id BIGINT REFERENCES kb_spaces(id) ON DELETE CASCADE;
        UPDATE wiki_pages SET space_id = {int(space_id)} WHERE space_id IS NULL;
        ALTER TABLE wiki_pages ALTER COLUMN space_id SET NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_wiki_pages_space ON wiki_pages (space_id) WHERE NOT is_deleted;

        ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS space_id BIGINT REFERENCES kb_spaces(id) ON DELETE CASCADE;
        UPDATE kb_documents SET space_id = {int(space_id)} WHERE space_id IS NULL;
        ALTER TABLE kb_documents ALTER COLUMN space_id SET NOT NULL;
        -- 同一份文件可以分别放进个人空间和企业空间：去重改为按空间
        ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_sha256_key;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_documents_space_sha ON kb_documents (space_id, sha256);
    """))


def _m013_eval_runs(conn: Connection) -> None:
    """准确率评测的运行记录：用例快照、逐题结果、通过率。"""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            id BIGSERIAL PRIMARY KEY,
            set_code VARCHAR(64) NOT NULL,
            set_name VARCHAR(200) NOT NULL,
            profile_id BIGINT REFERENCES llm_profiles(id) ON DELETE SET NULL,
            model_name VARCHAR(200),
            status VARCHAR(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
            total INT NOT NULL DEFAULT 0,
            done INT NOT NULL DEFAULT 0,
            scored INT NOT NULL DEFAULT 0,
            passed INT NOT NULL DEFAULT 0,
            cases JSONB NOT NULL DEFAULT '[]'::jsonb,
            results JSONB NOT NULL DEFAULT '[]'::jsonb,
            error TEXT,
            created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ
        );
    """))


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "core schema: users, audit, wiki, kb, agent_runs", _m001_core_schema),
    (2, "read-only SQL role for LLM/agent queries", _m002_reader_role),
    (3, "seed wiki from demo knowledge_chunks", _m003_seed_wiki),
    (4, "chat sessions, messages and user memories", _m004_conversations_and_memory),
    (5, "model profiles, managed tools and skills", _m005_models_tools_skills),
    (6, "python analysis sandbox tool", _m006_python_analysis_tool),
    (7, "store business schema replaces ecommerce demo", _m007_store_schema),
    (8, "phone count by product name", _m008_store_comments),
    (9, "exclude transfers and wholesale from store sales", _m009_drop_non_retail),
    (10, "mask phone and id numbers in remarks", _m010_mask_remarks),
    (11, "usage metering, plans, wallet, billing and market prefs", _m011_billing_and_market),
    (12, "organizations, knowledge spaces and system settings", _m012_orgs_spaces_settings),
    (13, "accuracy evaluation runs", _m013_eval_runs),
]


def _system_tables(conn: Connection) -> list[str]:
    from backend.database import is_system_table

    rows = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).all()
    return [r[0] for r in rows if is_system_table(r[0])]


def run_migrations() -> list[int]:
    """执行所有未执行的迁移，返回本次执行的版本号。"""
    applied_now: list[int] = []
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INT PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        done = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations")).all()}
        for version, name, func in MIGRATIONS:
            if version in done:
                continue
            logger.info("执行数据库迁移 %03d: %s", version, name)
            func(conn)
            conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"), {"v": version, "n": name})
            applied_now.append(version)
        # 每次启动都确保系统表对只读角色不可见（防御后续手工建表遗漏 REVOKE）
        if SQL_READER_ROLE and conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": SQL_READER_ROLE}).first():
            for table in _system_tables(conn):
                conn.execute(text(f"REVOKE ALL ON {quote_identifier(table)} FROM {quote_identifier(SQL_READER_ROLE)}"))
    return applied_now
