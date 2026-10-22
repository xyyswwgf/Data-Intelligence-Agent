import pytest
from sqlalchemy import text


def _db_available() -> bool:
    try:
        from backend.database import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM fact_orders LIMIT 1"))
        return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()

requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="PostgreSQL 未启动或未初始化（docker compose up -d）")
