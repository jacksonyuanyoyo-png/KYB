import os
import re
from collections.abc import Callable, Iterator

os.environ["LLM_ADAPTER"] = "noop"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.config import get_settings
from fcc_api.db.session import get_session
from fcc_api.main import create_app

_ROLE_USERS = {
    "ADVISOR": "u-advisor",
    "OPERATIONS": "u-ops",
    "COMPLIANCE": "u-compliance",
    "ADMIN": "u-admin",
}


def _redact_url(url: str) -> str:
    return re.sub(r"//([^:/@]+):([^@]+)@", r"//\1:***@", url)


def _public_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}".split("\n", 1)[0]
    message = re.sub(r":([^:@/\s]+)@", ":***@", message)
    message = re.sub(r"(?i)(password|secret)=([^\s]+)", r"\1=***", message)
    return message[:400]


def probe_database(url: str) -> tuple[bool, str]:
    probe = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 2})
    try:
        with probe.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        return False, _public_error(exc)
    else:
        return True, ""
    finally:
        probe.dispose()


@pytest.fixture(scope="session")
def app_database_probe() -> tuple[bool, str]:
    settings = get_settings()
    return probe_database(settings.database_url)


@pytest.fixture(scope="session")
def test_database() -> Iterator[tuple[Engine | None, str, str]]:
    url = get_settings().database_url_test
    reachable, reason = probe_database(url)
    if not reachable:
        yield None, reason, url
        return
    test_engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        yield test_engine, "", url
    finally:
        test_engine.dispose()


@pytest.fixture
def db_session(test_database: tuple[Engine | None, str, str]) -> Iterator[Session]:
    test_engine, reason, url = test_database
    if test_engine is None:
        pytest.skip(
            "测试库连不上，依赖数据库的测试已跳过。"
            f"检查的地址是 {_redact_url(url)}。原因：{reason}。"
            "请先启动仓库根目录已有的 PostgreSQL，并执行 "
            "docker compose exec postgres createdb -U fcc complex_accounts_test。"
            "不要修改 docker-compose。"
        )
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def role_headers() -> Callable[..., dict[str, str]]:
    def _headers(role: str, user_id: str | None = None) -> dict[str, str]:
        normalized = role.strip().upper()
        if normalized not in _ROLE_USERS:
            raise ValueError("role must be ADVISOR, OPERATIONS, COMPLIANCE, or ADMIN")
        return {
            "X-User-Id": user_id or _ROLE_USERS[normalized],
            "X-User-Role": normalized,
        }

    return _headers


@pytest.fixture
def client() -> Iterator[TestClient]:
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def db_client(db_session: Session) -> Iterator[TestClient]:
    application = create_app()

    def override_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_db] = override_session
    with TestClient(application) as test_client:
        yield test_client
