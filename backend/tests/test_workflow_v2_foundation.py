"""Issue #9: migrations, inbox/SYSTEM_BOARD, event_log, /meta, derive_status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from ak5.contracts.errors import ErrorCode
from ak5.contracts.events import EventType
from ak5.database import (
    migrate_sqlite_board_workflow_fields,
    migrate_sqlite_column_stages,
    migrate_sqlite_subscription_workflow_fields,
    migrate_sqlite_ticket_workflow_fields,
    run_sqlite_schema_migrations,
)
from ak5.models.base import Base
from ak5.models.board import Board
from ak5.models.column import Column
from ak5.models.event import Event
from ak5.seed import INBOX_BOARD_ID, seed_initial_data
from ak5.services.event_log import record_event
from ak5.services.templates import DEFAULT_TEMPLATE, get_template
from ak5.services.ticket_status import derive_status
from httpx import AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def test_default_template_stages():
    tmpl = get_template("default")
    assert tmpl is DEFAULT_TEMPLATE
    assert tmpl.max_attempts == 3
    stages = [c.stage for c in tmpl.columns]
    assert stages == [
        "backlog",
        "in_progress",
        "agent_review",
        "postmortem_pending",
        "human_review",
        "done",
    ]


def test_derive_status_rules():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    done = SimpleNamespace(
        stage="done",
        blocked_kind=None,
        blocked_by=None,
        claim_token=None,
        lease_expires_at=None,
    )
    assert derive_status(done, now=now) == "done"

    blocked = SimpleNamespace(
        stage="backlog",
        blocked_kind="needs_human",
        blocked_by="why",
        claim_token=None,
        lease_expires_at=None,
    )
    assert derive_status(blocked, now=now) == "blocked"

    claimed = SimpleNamespace(
        stage="backlog",
        blocked_kind=None,
        blocked_by=None,
        claim_token="tok",
        lease_expires_at=now + timedelta(hours=1),
    )
    assert derive_status(claimed, now=now) == "in_progress"

    expired = SimpleNamespace(
        stage="backlog",
        blocked_kind=None,
        blocked_by=None,
        claim_token="tok",
        lease_expires_at=now - timedelta(seconds=1),
    )
    assert derive_status(expired, now=now) == "open"

    open_t = SimpleNamespace(
        stage="backlog",
        blocked_kind=None,
        blocked_by=None,
        claim_token=None,
        lease_expires_at=None,
    )
    assert derive_status(open_t, now=now) == "open"


def test_migrate_v1_sqlite_schema_to_v2():
    """Existing v1 SQLite DB gains workflow columns and remapped stages."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                """
                CREATE TABLE actors (
                    actor_id VARCHAR(128) PRIMARY KEY,
                    actor_type VARCHAR(16) NOT NULL,
                    name VARCHAR(256) NOT NULL,
                    role VARCHAR(128) NOT NULL,
                    description TEXT,
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    status VARCHAR(16) NOT NULL DEFAULT 'idle',
                    avatar_url VARCHAR(512),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE boards (
                    board_id VARCHAR(128) PRIMARY KEY,
                    name VARCHAR(256) NOT NULL,
                    description TEXT,
                    created_by VARCHAR(128) NOT NULL,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(created_by) REFERENCES actors (actor_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE columns (
                    column_id VARCHAR(128) PRIMARY KEY,
                    board_id VARCHAR(128) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    stage VARCHAR(32) NOT NULL,
                    position INTEGER NOT NULL,
                    wip_limit INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(board_id) REFERENCES boards (board_id) ON DELETE CASCADE,
                    CONSTRAINT check_column_stage CHECK (
                        stage IN ('open', 'in_progress', 'review', 'done')
                    )
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE tickets (
                    ticket_id VARCHAR(64) PRIMARY KEY,
                    board_id VARCHAR(128) NOT NULL,
                    column_id VARCHAR(128) NOT NULL,
                    parent_ticket_id VARCHAR(64),
                    title VARCHAR(256) NOT NULL,
                    description TEXT,
                    priority VARCHAR(16) NOT NULL,
                    rank VARCHAR(128) NOT NULL,
                    labels TEXT NOT NULL,
                    assigned_to VARCHAR(128),
                    created_by VARCHAR(128) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    blocked_by TEXT,
                    execution_context TEXT,
                    due_date DATETIME,
                    is_archived BOOLEAN NOT NULL DEFAULT 0,
                    archived_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE subscriptions (
                    subscription_id VARCHAR(128) PRIMARY KEY,
                    board_id VARCHAR(128),
                    exec_command TEXT NOT NULL,
                    events TEXT,
                    for_agent VARCHAR(128),
                    ignore_actor VARCHAR(128),
                    debounce_seconds FLOAT NOT NULL DEFAULT 0.0,
                    created_by VARCHAR(128) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO actors (
                    actor_id, actor_type, name, role, capabilities, status, created_at, updated_at
                ) VALUES (
                    'user_pm', 'human', 'PM', 'PM', '[]', 'idle', '2026-01-01', '2026-01-01'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO boards (board_id, name, created_by, created_at)
                VALUES ('b1', 'Board', 'user_pm', '2026-01-01')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO columns (
                    column_id, board_id, name, stage, position, wip_limit, created_at
                ) VALUES
                    ('c_open', 'b1', 'To Do', 'open', 1, 0, '2026-01-01'),
                    ('c_rev', 'b1', 'Review', 'review', 2, 0, '2026-01-01')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO subscriptions (
                    subscription_id, board_id, exec_command, created_by, created_at
                ) VALUES ('sub1', 'b1', 'echo hi', 'user_pm', '2026-01-01')
                """
            )
        )

        migrate_sqlite_board_workflow_fields(conn)
        migrate_sqlite_ticket_workflow_fields(conn)
        migrate_sqlite_subscription_workflow_fields(conn)
        migrate_sqlite_column_stages(conn)

        board_cols = {r["name"] for r in conn.execute(text("PRAGMA table_info(boards)")).mappings()}
        assert {"kind", "template", "auto_created"} <= board_cols

        ticket_cols = {r["name"] for r in conn.execute(text("PRAGMA table_info(tickets)")).mappings()}
        assert {
            "original_request",
            "attempt",
            "max_attempts",
            "claim_token",
            "claimed_by",
            "lease_expires_at",
            "lease_expirations",
            "submitted_by",
            "blocked_kind",
            "blocked_since",
            "blocked_actor",
        } <= ticket_cols

        sub_cols = {
            r["name"] for r in conn.execute(text("PRAGMA table_info(subscriptions)")).mappings()
        }
        assert {"ack_cursor", "consumer_id", "consumer_lease_expires_at"} <= sub_cols
        exec_row = next(
            r
            for r in conn.execute(text("PRAGMA table_info(subscriptions)")).mappings()
            if r["name"] == "exec_command"
        )
        assert exec_row["notnull"] == 0

        stages = {
            row[0]
            for row in conn.execute(text("SELECT stage FROM columns")).fetchall()
        }
        assert stages == {"backlog", "agent_review"}
        assert "review" not in stages
        assert "open" not in stages

        # New stage values must satisfy rebuilt CHECK.
        conn.execute(
            text(
                """
                INSERT INTO columns (
                    column_id, board_id, name, stage, position, wip_limit, created_at
                ) VALUES (
                    'c_hr', 'b1', 'Human Review', 'human_review', 3, 0, '2026-01-01'
                )
                """
            )
        )


@pytest.mark.asyncio
async def test_fresh_db_seed_creates_inbox(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)

    # Rebind seed session factory to this fresh engine.
    from ak5 import database as dbmod
    from ak5 import seed as seedmod

    engine = create_async_engine(url, future=True)
    monkeypatch.setattr(dbmod, "engine", engine)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(dbmod, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(seedmod, "AsyncSessionLocal", session_factory)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_sqlite_schema_migrations(engine)
    await seed_initial_data()

    async with session_factory() as session:
        inbox = await session.get(Board, INBOX_BOARD_ID)
        assert inbox is not None
        assert inbox.kind == "inbox"
        cols = (
            await session.execute(select(Column).where(Column.board_id == INBOX_BOARD_ID))
        ).scalars().all()
        assert len(cols) == 1
        assert cols[0].stage == "unclassified"

        default = await session.get(Board, "proj-core-engine")
        assert default is not None
        assert default.template == "default"
        dcols = (
            await session.execute(
                select(Column).where(Column.board_id == "proj-core-engine")
            )
        ).scalars().all()
        stages = {c.stage for c in dcols}
        assert stages == {
            "backlog",
            "in_progress",
            "agent_review",
            "postmortem_pending",
            "human_review",
            "done",
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_record_event_seq_monotonic(test_db: AsyncSession):
    e1 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id=None,
        actor_id="user_pm",
        data={"title": "a"},
    )
    e2 = await record_event(
        test_db,
        EventType.WORK_AVAILABLE,
        board_id="inbox",
        ticket_id=None,
        actor_id="user_pm",
        data={"board_id": "inbox", "stage": "unclassified", "required_role": "triage"},
    )
    await test_db.commit()

    assert e1.seq is not None
    assert e2.seq is not None
    assert e2.seq == e1.seq + 1

    rows = (
        await test_db.execute(select(Event).order_by(Event.seq.asc()))
    ).scalars().all()
    assert [r.seq for r in rows] == [e1.seq, e2.seq]
    assert rows[0].type == EventType.TICKET_CREATED.value
    assert rows[1].type == EventType.WORK_AVAILABLE.value


@pytest.mark.asyncio
async def test_meta_endpoint_unauthenticated(anon_client: AsyncClient):
    resp = await anon_client.get("/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_version"] == "2"
    assert body["min_client"] == "2.0.0"
    assert body["capabilities"] == ["long_poll"]


@pytest.mark.asyncio
async def test_inbox_system_board_guards(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # Seeded inbox is visible
    boards = await client.get("/api/v1/boards", headers=headers)
    assert boards.status_code == 200
    ids = {b["board_id"] for b in boards.json()}
    assert INBOX_BOARD_ID in ids

    inbox = await client.get(f"/api/v1/boards/{INBOX_BOARD_ID}", headers=headers)
    assert inbox.status_code == 200
    assert inbox.json()["kind"] == "inbox"
    assert len(inbox.json()["columns"]) == 1
    assert inbox.json()["columns"][0]["stage"] == "unclassified"

    # Cannot add columns
    add_col = await client.post(
        f"/api/v1/boards/{INBOX_BOARD_ID}/columns",
        json={
            "column_id": "inbox_extra",
            "name": "Extra",
            "stage": "backlog",
            "position": 2,
            "wip_limit": 0,
        },
        headers=headers,
    )
    assert add_col.status_code == 403
    assert add_col.json()["error"]["code"] == ErrorCode.SYSTEM_BOARD.value

    # Cannot rename
    patch = await client.patch(
        f"/api/v1/boards/{INBOX_BOARD_ID}",
        json={"name": "Hacked"},
        headers=headers,
    )
    assert patch.status_code == 403
    assert patch.json()["error"]["code"] == ErrorCode.SYSTEM_BOARD.value

    # Cannot delete
    delete = await client.delete(f"/api/v1/boards/{INBOX_BOARD_ID}", headers=headers)
    assert delete.status_code == 403
    assert delete.json()["error"]["code"] == ErrorCode.SYSTEM_BOARD.value

    # Cannot recreate via create API
    recreate = await client.post(
        "/api/v1/boards",
        json={"board_id": "inbox", "name": "Fake Inbox", "template": "default"},
        headers=headers,
    )
    assert recreate.status_code == 403
    assert recreate.json()["error"]["code"] == ErrorCode.SYSTEM_BOARD.value


@pytest.mark.asyncio
async def test_create_board_uses_default_template(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")
    resp = await client.post(
        "/api/v1/boards",
        json={"board_id": "board-tmpl", "name": "Templated", "template": "default"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template"] == "default"
    assert body["kind"] == "standard"

    detail = await client.get("/api/v1/boards/board-tmpl", headers=headers)
    assert detail.status_code == 200
    stages = [c["stage"] for c in detail.json()["columns"]]
    assert stages == [
        "backlog",
        "in_progress",
        "agent_review",
        "postmortem_pending",
        "human_review",
        "done",
    ]
