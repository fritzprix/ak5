"""Pytest fixtures.

CRITICAL: env isolation MUST happen before any ``ak5.*`` import. Otherwise
``ak5.database.engine`` binds to the real ``~/.local/share/ak5/ak5.db`` and
tests like ``ak5 reset --yes`` wipe production boards.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

# --- Isolate production DB BEFORE importing ak5 (order matters) ---
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="ak5_pytest_data_"))
os.environ["AK5_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TEST_DATA_DIR / 'ak5.db').as_posix()}"
os.environ.setdefault("AK5_JWT_SECRET", "test-jwt-secret-not-for-production")
os.environ.pop("AK5_IDENTIFY_SECRET", None)
os.environ.pop("IDENTIFY_SECRET", None)

import ak5.models  # noqa: E402, F401
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from ak5.config import settings  # noqa: E402
from ak5.database import get_db  # noqa: E402
from ak5.main import app  # noqa: E402
from ak5.models.base import Base  # noqa: E402
from ak5.routers.auth import create_access_token  # noqa: E402
from ak5.security import reset_secret_caches  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

# Guard: fail collection if we somehow still point at the user global store.
_db_path = make_url(settings.DATABASE_URL).database or ""
if "/.local/share/ak5/" in _db_path.replace("\\", "/"):
    raise RuntimeError(
        f"Refusing to run tests against production DB: {settings.DATABASE_URL}. "
        "conftest isolation failed."
    )

# Use in-memory SQLite database for HTTP API tests (override get_db).
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(test_engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    # Match production database.py so FK mismatches fail in tests.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


TestAsyncSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture(autouse=True)
def _isolate_runtime_secrets(monkeypatch):
    """Stable JWT for tests; identify gate off unless a test enables it."""
    monkeypatch.setenv("AK5_JWT_SECRET", "test-jwt-secret-not-for-production")
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.delenv("IDENTIFY_SECRET", raising=False)
    monkeypatch.delenv("AK5_JWT_SECRET_FILE", raising=False)
    # Keep AK5_DATA_DIR / DATABASE_URL on the isolated temp dir for the whole session.
    monkeypatch.setenv("AK5_DATA_DIR", str(_TEST_DATA_DIR))
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    reset_secret_caches()
    yield
    reset_secret_caches()


@pytest_asyncio.fixture(scope="function")
async def test_db() -> AsyncGenerator[AsyncSession]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Override get_db
    async def override_get_db():
        async with TestAsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Seed initial test data
    async with TestAsyncSessionLocal() as session:
        # Seed default PM and agents
        import json

        from ak5.models.actor import Actor
        from ak5.models.board import Board
        from ak5.models.board_member import BoardMember
        from ak5.models.column import Column

        pm = Actor(
            actor_id="user_pm",
            actor_type="human",
            name="Project Manager",
            role="PM",
            description="Lead PM",
            capabilities=json.dumps(["management"]),
            status="idle",
        )
        agent = Actor(
            actor_id="agent_image_worker",
            actor_type="agent",
            name="Image Worker",
            role="Media Specialist",
            description="Image resizing and WebP conversion",
            capabilities=json.dumps(["image-resize", "webp"]),
            status="idle",
        )
        session.add_all([pm, agent])

        board = Board(
            board_id="proj-core-engine",
            name="Core Engine",
            description="Test board",
            created_by="user_pm",
        )
        session.add(board)

        members = [
            BoardMember(board_id="proj-core-engine", actor_id="user_pm", role="admin"),
            BoardMember(board_id="proj-core-engine", actor_id="agent_image_worker", role="agent"),
        ]
        session.add_all(members)

        cols = [
            Column(column_id="col_todo", board_id="proj-core-engine", name="To Do", stage="open", position=1, wip_limit=0),
            Column(
                column_id="col_in_progress",
                board_id="proj-core-engine",
                name="In Progress",
                stage="in_progress",
                position=2,
                wip_limit=3,
            ),
            Column(column_id="col_review", board_id="proj-core-engine", name="Review", stage="review", position=3, wip_limit=3),
            Column(column_id="col_done", board_id="proj-core-engine", name="Done", stage="done", position=4, wip_limit=0),
        ]
        session.add_all(cols)
        await session.commit()

    async with TestAsyncSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client(test_db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Authenticated as user_pm by default (read endpoints now require JWT)."""
    transport = ASGITransport(app=app)
    token = create_access_token("user_pm", "human")
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac


@pytest_asyncio.fixture(scope="function")
async def anon_client(test_db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Unauthenticated client for auth-gate tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_headers():
    def _headers(actor_id: str = "user_pm", actor_type: str = "human") -> dict[str, str]:
        token = create_access_token(actor_id, actor_type)
        return {"Authorization": f"Bearer {token}"}

    return _headers
