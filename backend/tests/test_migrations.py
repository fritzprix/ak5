"""#105: Alembic baseline, legacy stamping, locks (deployment.md §1.2)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from ak5.server import migrations as mig
from ak5.server.database import run_legacy_schema_upgrade
from ak5.server.models.base import Base
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _engine(tmp_path: Path, name: str = "ak5.db") -> AsyncEngine:
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}", future=True)


def _diff(sync_conn):
    ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
    return compare_metadata(ctx, Base.metadata)


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))


@pytest.mark.asyncio
async def test_fresh_database_gets_baseline_and_matches_the_models(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        revision = await mig.ensure_schema(engine, legacy_upgrade=run_legacy_schema_upgrade)
        assert revision == mig.head_revision()
        async with engine.connect() as conn:
            drift = await conn.run_sync(_diff)
        # A model change without a new Alembic revision lands here.
        assert drift == []
        # #97 passkeys are part of the baseline.
        assert {"passkey_credentials", "webauthn_challenges", "alembic_version"} <= await _tables(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_schema_is_idempotent(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        first = await mig.ensure_schema(engine)
        before = await _tables(engine)
        assert await mig.ensure_schema(engine) == first
        assert await _tables(engine) == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pre_alembic_database_is_upgraded_in_place_and_stamped(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        # A database as the server created it before Alembic: tables, no alembic_version.
        async with engine.begin() as conn:
            await conn.run_sync(mig.adopt_baseline_sync)  # baseline shape, no alembic_version
            await conn.execute(
                text("INSERT INTO workspaces (workspace_id, name, policy) VALUES ('ws_keep', 'Keep me', '{}')")
            )
        assert "alembic_version" not in await _tables(engine)

        revision = await mig.ensure_schema(engine, legacy_upgrade=run_legacy_schema_upgrade)

        assert revision == mig.head_revision()
        async with engine.connect() as conn:
            name = (await conn.execute(text("SELECT name FROM workspaces WHERE workspace_id='ws_keep'"))).scalar()
        assert name == "Keep me"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_database_with_tables_is_refused_without_legacy_path(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE something_else (id INTEGER PRIMARY KEY)"))
        with pytest.raises(mig.SchemaError, match="not created by ak5"):
            await mig.ensure_schema(engine)  # no legacy_upgrade → refuse to guess
        assert "alembic_version" not in await _tables(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_downgrade_to_base_drops_everything(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        await mig.ensure_schema(engine)
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: mig.command.downgrade(mig.alembic_config(c), "base"))
        assert await _tables(engine) - {"alembic_version"} == set()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_auto_migrate_off_only_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ak5.server.database import ensure_database_schema

    monkeypatch.setenv("AK5_AUTO_MIGRATE", "0")
    assert not mig.auto_migrate_enabled()
    engine = _engine(tmp_path)
    try:
        with pytest.raises(mig.SchemaError, match="ak5 db upgrade"):
            await ensure_database_schema(engine)
        assert await _tables(engine) == set()  # nothing was created

        monkeypatch.setenv("AK5_AUTO_MIGRATE", "1")
        await ensure_database_schema(engine)
        monkeypatch.setenv("AK5_AUTO_MIGRATE", "0")
        assert await ensure_database_schema(engine) == mig.head_revision()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_migration_lock_waits_then_times_out_and_clears_stale_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = _engine(tmp_path)
    lock = tmp_path / "ak5.db.migrate.lock"
    try:
        async with mig.migration_lock(engine):
            assert lock.exists()
            monkeypatch.setattr(mig, "_LOCK_TIMEOUT_SECONDS", 0.5)
            with pytest.raises(mig.SchemaError, match="Timed out"):
                async with mig.migration_lock(engine):
                    pass
        assert not lock.exists()

        lock.write_text("999999")  # a crashed migrator
        old = time.time() - mig._LOCK_STALE_SECONDS - 5
        os.utime(lock, (old, old))
        async with mig.migration_lock(engine):
            assert lock.exists()
        assert not lock.exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_0004_backfills_existing_attachments(tmp_path: Path):
    """Test #134: 0004_workspace_usage upgrade backfills existing ticket_attachments into workspace_usage."""
    engine = _engine(tmp_path)
    try:
        # Upgrade to 0003_rate_counters
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: mig.command.upgrade(mig.alembic_config(c), "0003_rate_counters"))

        # Insert ticket_attachments prior to 0004
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO ticket_attachments (attachment_id, workspace_id, ticket_id, actor_id, filename, file_size, content_type, storage_path) "
                    "VALUES ('att_1', 'ws_bf', 'TK_1', 'act_1', 'file1.txt', 1500, 'text/plain', 'p1'), "
                    "       ('att_2', 'ws_bf', 'TK_1', 'act_1', 'file2.txt', 3500, 'text/plain', 'p2')"
                )
            )

        # Now upgrade to head (which includes 0004_workspace_usage)
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: mig.command.upgrade(mig.alembic_config(c), "0004_workspace_usage"))

        # Verify workspace_usage table was populated with 5000 bytes for ws_bf
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text("SELECT value FROM workspace_usage WHERE workspace_id = 'ws_bf' AND key = 'attachment_bytes'")
                )
            ).one_or_none()
            assert row is not None
            assert row[0] == 5000
    finally:
        await engine.dispose()

