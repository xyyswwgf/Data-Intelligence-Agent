import os
import tempfile

# 必须在导入 backend.* 之前设置：配置在模块导入时读取
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "Admin12345")
os.environ["KB_INDEX_ASYNC"] = "false"  # 索引同步执行，测试结果可预期
os.environ.setdefault("EMBEDDING_PROVIDER", "none")  # 默认不依赖外部 Embedding 服务
os.environ.setdefault("KB_STORAGE_DIR", tempfile.mkdtemp(prefix="kb_test_"))

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402


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


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as c:  # 触发 lifespan：迁移 + 初始化管理员
        yield c


def _login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture(scope="session")
def admin_headers(client):
    return _login(client, os.environ["ADMIN_USERNAME"], os.environ["ADMIN_PASSWORD"])


@pytest.fixture(scope="session")
def make_user(client, admin_headers):
    """创建（或复用）指定角色的测试用户，返回其登录头。"""
    import uuid

    def factory(role: str):
        username = f"test_{role}_{uuid.uuid4().hex[:6]}"
        password = "Passw0rd123"
        resp = client.post("/api/admin/users", headers=admin_headers,
                           json={"username": username, "password": password, "role": role})
        assert resp.status_code == 201, resp.text
        return _login(client, username, password)

    return factory
