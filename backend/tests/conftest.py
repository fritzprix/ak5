from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from ak5.database import get_db
from ak5.main import app
from ak5.models.base import Base
from ak5.routers.auth import create_access_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Use in-memory SQLite database for tests
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

        cols = [
            Column(column_id="col_todo", board_id="proj-core-engine", name="To Do", stage="open", position=1, wip_limit=0),
            Column(column_id="col_in_progress", board_id="proj-core-engine", name="In Progress", stage="in_progress", position=2, wip_limit=3),
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_headers():
    def _headers(actor_id: str = "user_pm", actor_type: str = "human") -> dict[str, str]:
        token = create_access_token(actor_id, actor_type)
        return {"Authorization": f"Bearer {token}"}
    return _headers
