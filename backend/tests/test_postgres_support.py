"""PostgreSQL dialect, pool configuration, and Row-Level Security (RLS) tests (#106).

Tests URL normalization, pool options, SQLite vs PostgreSQL dialect behavior,
and verifies PostgreSQL RLS blocks cross-workspace data access at the database level.
"""

from __future__ import annotations

import os
import uuid

import pytest
from ak5.server.database import get_engine_options, normalize_database_url
from ak5.server.rls import (
    apply_postgres_rls,
    set_workspace_rls_context,
)
from sqlalchemy.engine import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def test_normalize_database_url():
    """Verify postgresql:// and postgres:// are normalized to asyncpg driver."""
    assert (
        normalize_database_url("postgresql://user:pass@localhost:5432/ak5")
        == "postgresql+asyncpg://user:pass@localhost:5432/ak5"
    )
    assert (
        normalize_database_url("postgres://user:pass@localhost:5432/ak5")
        == "postgresql+asyncpg://user:pass@localhost:5432/ak5"
    )
    assert (
        normalize_database_url("sqlite+aiosqlite:///path/to/ak5.db")
        == "sqlite+aiosqlite:///path/to/ak5.db"
    )


def test_get_engine_options():
    """Verify engine options differ properly between SQLite and PostgreSQL."""
    # SQLite
    url_sq, conn_args_sq, kwargs_sq = get_engine_options("sqlite+aiosqlite:///:memory:")
    assert "timeout" in conn_args_sq
    assert "pool_size" not in kwargs_sq

    # PostgreSQL
    url_pg, conn_args_pg, kwargs_pg = get_engine_options("postgresql://user:pass@localhost:5432/ak5")
    assert url_pg.startswith("postgresql+asyncpg://")
    assert kwargs_pg.get("pool_size") == 5
    assert kwargs_pg.get("max_overflow") == 10
    assert kwargs_pg.get("pool_timeout") == 30.0

    # Unsupported scheme raises RuntimeError
    with pytest.raises(RuntimeError, match="Unsupported DATABASE_URL scheme"):
        get_engine_options("mysql+aiomysql://root@localhost/ak5")


def test_sqlite_rls_is_noop():
    """Verify apply_postgres_rls is a no-op on SQLite engines."""
    sync_engine = create_engine("sqlite:///:memory:")
    with sync_engine.connect() as conn:
        # Must not raise error
        apply_postgres_rls(conn)


@pytest.mark.asyncio
async def test_set_workspace_rls_context_sqlite_noop():
    """Verify set_workspace_rls_context is a safe no-op on SQLite async sessions."""
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(bind=async_engine, class_=AsyncSession)
    async with maker() as session:
        await set_workspace_rls_context(session, "ws_test")
    await async_engine.dispose()


@pytest.mark.asyncio
async def test_live_postgres_rls_cross_workspace_blocking():
    """Verify that on PostgreSQL, RLS blocks cross-workspace reads and writes at the DB level (#106)."""
    pg_url = os.environ.get("AK5_TEST_POSTGRES_URL") or os.environ.get("AK5_POSTGRES_URL")
    if not pg_url:
        pytest.skip("AK5_TEST_POSTGRES_URL is not set; skipping live PostgreSQL RLS test")

    from ak5.server import migrations as mig
    from ak5.server.models.actor import Actor
    from ak5.server.models.board import Board
    from ak5.server.models.workspace import Workspace
    from sqlalchemy import select

    engine = create_async_engine(pg_url, future=True)
    # Ensure all tables and RLS policies are applied
    await mig.ensure_schema(engine)

    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    run_id = uuid.uuid4().hex[:6]
    ws_alpha = f"ws_alpha_{run_id}"
    ws_beta = f"ws_beta_{run_id}"
    board_alpha_id = f"b_alpha_{run_id}"
    board_beta_id = f"b_beta_{run_id}"

    # Setup: Create workspaces, actor, and boards
    async with maker() as session:
        if await session.get(Actor, "user_pm") is None:
            session.add(
                Actor(
                    actor_id="user_pm",
                    name="Product Manager",
                    actor_type="human",
                    role="pm",
                )
            )
        session.add(Workspace(workspace_id=ws_alpha, name="Workspace Alpha", policy="{}"))
        session.add(Workspace(workspace_id=ws_beta, name="Workspace Beta", policy="{}"))

        session.add(
            Board(
                board_id=board_alpha_id,
                workspace_id=ws_alpha,
                name="Alpha Board",
                created_by="user_pm",
                kind="standard",
                template="default",
            )
        )
        session.add(
            Board(
                board_id=board_beta_id,
                workspace_id=ws_beta,
                name="Beta Board",
                created_by="user_pm",
                kind="standard",
                template="default",
            )
        )
        await session.commit()

    # 1. Transaction scoped to ws_alpha:
    # Query without where clause (simulating bug where app omits workspace filter)
    async with maker() as session:
        await set_workspace_rls_context(session, ws_alpha)

        # Unfiltered query
        boards = (await session.execute(select(Board))).scalars().all()
        board_ids = {b.board_id for b in boards}

        assert board_alpha_id in board_ids, "Alpha board must be visible in ws_alpha"
        assert board_beta_id not in board_ids, "Beta board must be hidden by RLS in ws_alpha"

        # Explicit query for Beta board in Alpha context must return nothing
        beta_query = (
            await session.execute(select(Board).where(Board.board_id == board_beta_id))
        ).scalar_one_or_none()
        assert beta_query is None, "Cross-workspace board query must be blocked by RLS"

    # 2. Transaction scoped to ws_beta:
    async with maker() as session:
        await set_workspace_rls_context(session, ws_beta)

        boards = (await session.execute(select(Board))).scalars().all()
        board_ids = {b.board_id for b in boards}

        assert board_beta_id in board_ids, "Beta board must be visible in ws_beta"
        assert board_alpha_id not in board_ids, "Alpha board must be hidden by RLS in ws_beta"

    # 3. Write violation: attempting to insert row with workspace_id != current ak5.workspace_id
    async with maker() as session:
        await set_workspace_rls_context(session, ws_alpha)
        session.add(
            Board(
                board_id=f"b_leak_{run_id}",
                workspace_id=ws_beta,  # Mismatch: inserting beta row in alpha context
                name="Leak Board",
                created_by="user_pm",
                kind="standard",
                template="default",
            )
        )
        with pytest.raises(Exception) as exc_info:
            await session.commit()
        # PostgreSQL raises error mentioning row-level security policy
        err_msg = str(exc_info.value).lower()
        assert "row-level security" in err_msg or "violates" in err_msg

    await engine.dispose()
