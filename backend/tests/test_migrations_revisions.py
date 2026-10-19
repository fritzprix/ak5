"""#105 review: pre-Alembic databases must take *later* revisions, not skip them."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ak5.server import migrations as mig
from ak5.server.database import run_legacy_schema_upgrade
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

FAKE_NEXT = '''
"""fake next revision: one new column on an existing table, one new table"""
import sqlalchemy as sa
from alembic import op

revision = "zz_fake_next"
down_revision = "__REAL_HEAD__"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("note", sa.Text(), nullable=True))
    op.create_table("extra_things", sa.Column("thing_id", sa.String(32), primary_key=True))


def downgrade() -> None:
    op.drop_table("extra_things")
    op.drop_column("workspaces", "note")
'''


@pytest.fixture
def with_fake_next(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the migration machinery at a copy of the package with one more revision on top."""
    script_dir = tmp_path / "migrations_copy"
    shutil.copytree(
        Path(mig.__file__).resolve().parent,
        script_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    real_head = mig.head_revision()  # chain after whatever the real head is today
    (script_dir / "versions" / "zz_fake_next.py").write_text(
        FAKE_NEXT.replace("__REAL_HEAD__", real_head), encoding="utf-8"
    )

    real = mig.alembic_config

    def patched(connection=None):
        cfg = real(connection)
        cfg.set_main_option("script_location", str(script_dir))
        return cfg

    monkeypatch.setattr(mig, "alembic_config", patched)
    return "zz_fake_next"


def _engine(tmp_path: Path) -> AsyncEngine:
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ak5.db'}", future=True)


async def _columns(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as conn:
        return {c["name"] for c in await conn.run_sync(lambda s: inspect(s).get_columns(table))}


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        return set(await conn.run_sync(lambda s: inspect(s).get_table_names()))


@pytest.mark.asyncio
async def test_pre_alembic_database_gets_later_revisions_applied(
    tmp_path: Path, with_fake_next: str
):
    engine = _engine(tmp_path)
    try:
        async with engine.begin() as conn:  # a database from before Alembic
            await conn.run_sync(mig.adopt_baseline_sync)  # baseline shape, no alembic_version
            await conn.execute(
                text("INSERT INTO workspaces (workspace_id, name, policy) VALUES ('ws_keep', 'Keep', '{}')")
            )
        assert mig.head_revision() == with_fake_next

        revision = await mig.ensure_schema(engine, legacy_upgrade=run_legacy_schema_upgrade)

        assert revision == with_fake_next
        assert "note" in await _columns(engine, "workspaces")  # column change was applied
        assert "extra_things" in await _tables(engine)  # new table was created exactly once
        async with engine.connect() as conn:
            name = (await conn.execute(text("SELECT name FROM workspaces"))).scalar_one()
        assert name == "Keep"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fresh_database_runs_baseline_then_later_revisions(tmp_path: Path, with_fake_next: str):
    engine = _engine(tmp_path)
    try:
        assert await mig.ensure_schema(engine) == with_fake_next
        assert "note" in await _columns(engine, "workspaces")
        assert "extra_things" in await _tables(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_adoption_creates_only_missing_baseline_tables(tmp_path: Path):
    """A pre-Alembic file lacking newer tables gets them from the baseline; others stay as they are."""
    engine = _engine(tmp_path)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(mig.adopt_baseline_sync)  # baseline shape, no alembic_version
            await conn.execute(text("DROP TABLE passkey_credentials"))
            await conn.execute(text("DROP TABLE webauthn_challenges"))
            await conn.execute(
                text("INSERT INTO workspaces (workspace_id, name, policy) VALUES ('ws_keep', 'Keep', '{}')")
            )
        assert await mig.ensure_schema(engine, legacy_upgrade=run_legacy_schema_upgrade) == mig.head_revision()
        assert {"passkey_credentials", "webauthn_challenges"} <= await _tables(engine)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM workspaces"))).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_adoption_supplements_missing_baseline_indexes(tmp_path: Path):
    """#120: pre-Alembic tables missing baseline indexes get them supplemented via CREATE INDEX IF NOT EXISTS."""
    engine = _engine(tmp_path)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(mig.adopt_baseline_sync)
            await conn.execute(
                text("INSERT INTO workspaces (workspace_id, name, policy) VALUES ('ws_keep', 'Keep', '{}')")
            )
            # Explicitly drop baseline indexes on an adopted table
            await conn.execute(text("DROP INDEX idx_tickets_assigned"))
            await conn.execute(text("DROP INDEX ix_tickets_workspace_id"))

        async with engine.connect() as conn:
            indexes_before = await conn.run_sync(lambda c: {ix["name"] for ix in inspect(c).get_indexes("tickets")})
            assert "idx_tickets_assigned" not in indexes_before
            assert "ix_tickets_workspace_id" not in indexes_before

        revision = await mig.ensure_schema(engine, legacy_upgrade=run_legacy_schema_upgrade)
        assert revision == mig.head_revision()

        async with engine.connect() as conn:
            indexes_after = await conn.run_sync(lambda c: {ix["name"] for ix in inspect(c).get_indexes("tickets")})
            assert "idx_tickets_assigned" in indexes_after
            assert "ix_tickets_workspace_id" in indexes_after
            name = (await conn.execute(text("SELECT name FROM workspaces WHERE workspace_id = 'ws_keep'"))).scalar_one()
            assert name == "Keep"
    finally:
        await engine.dispose()
