import os
import tempfile

# 必须在导入 backend.* 之前设置：配置在模块导入时读取
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "Admin12345")
os.environ["KB_INDEX_ASYNC"] = "false"  # 索引同步执行，测试结果可预期
os.environ.setdefault("EMBEDDING_PROVIDER", "none")  # 默认不依赖外部 Embedding 服务
os.environ.setdefault("KB_STORAGE_DIR", tempfile.mkdtemp(prefix="kb_test_"))

# 测试使用独立的数据库（<DB_NAME>_test），写入虚构样例数据，不触碰开发库里的真实门店数据。
# 显式设置 DATABASE_URL 时按其执行（CI 等场景自行准备数据库）。
if not os.getenv("DATABASE_URL"):
    os.environ["DB_NAME"] = os.getenv("TEST_DB_NAME") or f"{os.getenv('DB_NAME', 'data_agent_db')}_test"

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _ensure_database() -> None:
    """测试库不存在时创建（连接同一实例的 postgres 库执行 CREATE DATABASE）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from backend.config import DATABASE_URL

    url = make_url(DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).first()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def _prepare() -> bool:
    try:
        if not os.getenv("DATABASE_URL"):
            _ensure_database()
        from backend.database import get_connection
        from backend.migrations import run_migrations
        from backend.store_data.sample import load_sample

        run_migrations()
        with get_connection() as conn:
            load_sample(conn)
        return True
    except Exception as exc:  # 数据库不可用时，依赖数据库的用例自动跳过
        print(f"测试数据库不可用：{exc}")
        return False


DB_AVAILABLE = _prepare()

requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="PostgreSQL 未启动（docker compose up -d postgres）")


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
