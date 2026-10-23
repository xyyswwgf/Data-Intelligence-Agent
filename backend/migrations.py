"""
轻量数据库迁移器 — 启动时按版本顺序执行，已执行的版本记录在 schema_migrations

客户环境升级只需替换镜像并重启服务；迁移在 PostgreSQL advisory lock 下执行，
多个后端实例同时启动也不会重复执行。
"""

from __future__ import annotations

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


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "core schema: users, audit, wiki, kb, agent_runs", _m001_core_schema),
    (2, "read-only SQL role for LLM/agent queries", _m002_reader_role),
    (3, "seed wiki from demo knowledge_chunks", _m003_seed_wiki),
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
