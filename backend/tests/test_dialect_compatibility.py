"""Dialect-specific SQL and PostgreSQL compatibility tests (#135).

Ensures dialect-specific raw/upsert statements (such as GREATEST vs max for clamps)
compile and execute correctly on PostgreSQL as well as SQLite.
"""

from __future__ import annotations

import os

import pytest
from ak5.server.models.rate_counter import RateCounter
from ak5.server.models.workspace import WorkspaceUsage
from ak5.server.quota import build_record_attachment_bytes_stmt
from ak5.server.ratelimit import _upsert_for
from sqlalchemy.dialects import postgresql, sqlite


def test_record_attachment_bytes_dialect_compilation():
    """Verify record_attachment_bytes compiles correctly for PostgreSQL (GREATEST) and SQLite (max)."""
    # PostgreSQL compilation
    stmt_pg = build_record_attachment_bytes_stmt("postgresql", "ws_test", 1024)
    compiled_pg = str(stmt_pg.compile(dialect=postgresql.dialect()))
    assert "greatest" in compiled_pg.lower(), "PostgreSQL requires GREATEST for multi-argument clamp"
    assert "on conflict" in compiled_pg.lower()
    assert "workspace_usage" in compiled_pg.lower()

    # SQLite compilation
    stmt_sqlite = build_record_attachment_bytes_stmt("sqlite", "ws_test", 1024)
    compiled_sqlite = str(stmt_sqlite.compile(dialect=sqlite.dialect()))
    assert "max" in compiled_sqlite.lower(), "SQLite uses max for multi-argument clamp"
    assert "on conflict" in compiled_sqlite.lower()
    assert "workspace_usage" in compiled_sqlite.lower()

    # Unsupported dialect raises RuntimeError
    with pytest.raises(RuntimeError, match="Workspace usage does not support"):
        build_record_attachment_bytes_stmt("mysql", "ws_test", 1024)


def test_rate_counter_upsert_dialect_compilation():
    """Verify RateCounter atomic upsert compiles correctly on PostgreSQL and SQLite dialects."""
    # PostgreSQL compilation
    insert_pg = _upsert_for("postgresql")
    stmt_pg = (
        insert_pg(RateCounter)
        .values(key="ip:127.0.0.1", window_start=1700000000, count=1)
        .on_conflict_do_update(
            index_elements=[RateCounter.key, RateCounter.window_start],
            set_={"count": RateCounter.count + 1},
        )
        .returning(RateCounter.count)
    )
    compiled_pg = str(stmt_pg.compile(dialect=postgresql.dialect()))
    assert "on conflict (key, window_start)" in compiled_pg.lower()
    assert "returning rate_counters.count" in compiled_pg.lower()

    # SQLite compilation
    insert_sqlite = _upsert_for("sqlite")
    stmt_sqlite = (
        insert_sqlite(RateCounter)
        .values(key="ip:127.0.0.1", window_start=1700000000, count=1)
        .on_conflict_do_update(
            index_elements=[RateCounter.key, RateCounter.window_start],
            set_={"count": RateCounter.count + 1},
        )
        .returning(RateCounter.count)
    )
    compiled_sqlite = str(stmt_sqlite.compile(dialect=sqlite.dialect()))
    assert 'on conflict ("key", window_start)' in compiled_sqlite.lower()
    assert "returning count" in compiled_sqlite.lower()

    # Unsupported dialect raises RuntimeError
    with pytest.raises(RuntimeError, match="Rate limiting does not support"):
        _upsert_for("oracle")


def test_reconcile_attachment_bytes_dialect_compilation():
    """Verify WorkspaceUsage upsert in reconcile_attachment_bytes compiles on PG and SQLite."""
    # PostgreSQL
    insert_pg = _upsert_for("postgresql")
    stmt_pg = (
        insert_pg(WorkspaceUsage)
        .values(workspace_id="ws1", key="attachment_bytes", value=500)
        .on_conflict_do_update(
            index_elements=[WorkspaceUsage.workspace_id, WorkspaceUsage.key],
            set_={"value": 500},
        )
    )
    compiled_pg = str(stmt_pg.compile(dialect=postgresql.dialect()))
    assert "on conflict (workspace_id, key)" in compiled_pg.lower()

    # SQLite
    insert_sqlite = _upsert_for("sqlite")
    stmt_sqlite = (
        insert_sqlite(WorkspaceUsage)
        .values(workspace_id="ws1", key="attachment_bytes", value=500)
        .on_conflict_do_update(
            index_elements=[WorkspaceUsage.workspace_id, WorkspaceUsage.key],
            set_={"value": 500},
        )
    )
    compiled_sqlite = str(stmt_sqlite.compile(dialect=sqlite.dialect()))
    assert 'on conflict (workspace_id, "key")' in compiled_sqlite.lower()


@pytest.mark.asyncio
async def test_live_postgres_migrations_and_lock():
    """When AK5_TEST_POSTGRES_URL is set, test migration_lock and ensure_schema against PostgreSQL."""
    pg_url = os.environ.get("AK5_TEST_POSTGRES_URL") or os.environ.get("AK5_POSTGRES_URL")
    if not pg_url:
        pytest.skip("AK5_TEST_POSTGRES_URL is not set; skipping live PostgreSQL test")

    from ak5.server import migrations as mig
    from ak5.server.models.base import Base
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg_url, future=True)
    try:
        # Test advisory lock
        async with mig.migration_lock(engine):
            pass

        # Test ensure_schema
        revision = await mig.ensure_schema(engine)
        assert revision == mig.head_revision()

        # Check metadata match
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn, opts={"compare_type": True}),
                    Base.metadata,
                )
            )
        assert diff == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_postgres_attachment_and_ratelimit_upsert():
    """When AK5_TEST_POSTGRES_URL is set, verify execution against a live PostgreSQL instance."""
    pg_url = os.environ.get("AK5_TEST_POSTGRES_URL") or os.environ.get("AK5_POSTGRES_URL")
    if not pg_url:
        pytest.skip("AK5_TEST_POSTGRES_URL is not set; skipping live PostgreSQL test")

    import uuid

    from ak5.server import migrations as mig
    from ak5.server.quota import reconcile_attachment_bytes, record_attachment_bytes
    from ak5.server.ratelimit import hit
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_url, future=True)
    await mig.ensure_schema(engine)

    ws_id = f"ws_pg_{uuid.uuid4().hex[:8]}"
    rate_key = f"test_pg_{uuid.uuid4().hex[:8]}"

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        # 1. Test attachment bytes upsert with GREATEST clamp on PostgreSQL
        val1 = await record_attachment_bytes(session, ws_id, 1000)
        assert val1 == 1000
        val2 = await record_attachment_bytes(session, ws_id, 500)
        assert val2 == 1500
        # Clamped at 0 (cannot drop below 0 via GREATEST(0, value + delta))
        val3 = await record_attachment_bytes(session, ws_id, -2000)
        assert val3 == 0

        # 2. Test reconcile_attachment_bytes on PostgreSQL
        res = await reconcile_attachment_bytes(session, workspace_id=ws_id)
        assert res.get(ws_id) == 0

        # 3. Test rate limit upsert on PostgreSQL (with unique key per run)
        hit1 = await hit(session, rate_key)
        assert hit1.count == 1
        hit2 = await hit(session, rate_key)
        assert hit2.count == 2

    await engine.dispose()
