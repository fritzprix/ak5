"""v1 → v2 migration gate using scripts/make_v1_fixture_db.py (#29)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ak5.contracts.stages import STANDARD_STAGES
from ak5.database import run_sqlite_schema_migrations
from ak5.models.base import Base
from ak5.seed import seed_initial_data
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKE_V1 = REPO_ROOT / "scripts" / "make_v1_fixture_db.py"


def _run_make_v1(db_path: Path) -> int:
    proc = subprocess.run(
        [sys.executable, str(MAKE_V1), str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    # "wrote … with N tickets"
    return int(proc.stdout.strip().rsplit(" ", 2)[-2])


@pytest.mark.asyncio
async def test_v1_fixture_migration_idempotent(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = tmp_path / "v1_fixture.db"
    ticket_count = _run_make_v1(db_path)

    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AK5_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AK5_SKIP_DOTENV", "1")

    from ak5 import database as dbmod
    from ak5 import seed as seedmod

    engine = create_async_engine(url, future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    monkeypatch.setattr(dbmod, "engine", engine)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(dbmod, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(seedmod, "AsyncSessionLocal", session_factory)

    async def _boot_once() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await run_sqlite_schema_migrations(engine)
        await seed_initial_data()

    await _boot_once()
    await _boot_once()  # idempotent second pass

    legacy_path = data_dir / "legacy_subscriptions.json"
    assert legacy_path.is_file()
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert len(legacy) == 1
    assert legacy[0]["exec_command"] == "echo hi"

    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall() == []
        assert (await conn.execute(text("PRAGMA integrity_check"))).scalar() == "ok"
        n = (await conn.execute(text("SELECT COUNT(*) FROM tickets"))).scalar()
        assert n == ticket_count

        stages = {
            row[0]
            for row in (
                await conn.execute(text("SELECT stage FROM columns WHERE board_id = 'b1'"))
            ).fetchall()
        }
        assert "open" not in stages and "review" not in stages
        assert {s.value for s in STANDARD_STAGES} <= stages

        t_ip = (
            await conn.execute(
                text(
                    """
                    SELECT c.stage, t.status FROM tickets t
                    JOIN columns c ON c.column_id = t.column_id
                    WHERE t.ticket_id = 't_ip'
                    """
                )
            )
        ).one()
        assert t_ip[0] == "backlog"
        assert t_ip[1] == "open"

    await engine.dispose()
