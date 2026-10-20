"""v1 → v2 migration gate using scripts/make_v1_fixture_db.py (#29)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ak5.contracts.stages import STANDARD_STAGES
from ak5.seed import seed_initial_data
from ak5.server.database import run_legacy_schema_upgrade
from ak5.server.migrations import current_revision, ensure_schema, head_revision
from ak5.server.models.ticket import Ticket
from sqlalchemy import event, select, text
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
    monkeypatch.setattr(dbmod, "AsyncSessionLocalAdmin", session_factory)

    async def _boot_once() -> None:
        # The real startup path (#105): legacy fixes on a pre-Alembic file, then stamp head.
        await ensure_schema(engine, legacy_upgrade=run_legacy_schema_upgrade)
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
        assert await conn.run_sync(current_revision) == head_revision()  # stamped, not re-created
        assert "open" not in stages and "review" not in stages
        assert {s.value for s in STANDARD_STAGES} <= stages

        t_ip_stage = (
            await conn.execute(
                text(
                    """
                    SELECT c.stage FROM tickets t
                    JOIN columns c ON c.column_id = t.column_id
                    WHERE t.ticket_id = 't_ip'
                    """
                )
            )
        ).scalar_one()
        assert t_ip_stage == "backlog"

        # #35: status is derived only — the stored column and its CHECK are gone.
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(tickets)"))).fetchall()}
        assert "status" not in cols
        ddl = (
            await conn.execute(text("SELECT sql FROM sqlite_master WHERE name = 'tickets'"))
        ).scalar_one()
        assert "check_ticket_status" not in ddl
        indexes = {
            r[1] for r in (await conn.execute(text("PRAGMA index_list(tickets)"))).fetchall()
        }
        assert {"idx_tickets_board", "idx_tickets_column_rank", "idx_tickets_parent", "ix_tickets_workspace_id"} <= indexes

        # #128: adopted tables must have workspace_id column and baseline indexes supplemented
        for t in ("boards", "columns", "tickets", "subscriptions"):
            t_cols = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info('{t}')"))).fetchall()}
            assert "workspace_id" in t_cols, f"{t} missing workspace_id"
            t_indexes = {r[1] for r in (await conn.execute(text(f"PRAGMA index_list('{t}')"))).fetchall()}
            assert f"ix_{t}_workspace_id" in t_indexes, f"{t} missing ix_{t}_workspace_id"


        # #79: rewriting actor refs must not touch updated_at. Only t_ip is a real
        # change (claimless in_progress → backlog by repair_claimless_in_progress_tickets).
        rows = (
            await conn.execute(
                text("SELECT ticket_id, created_by, updated_at FROM tickets WHERE ticket_id != 't_ip'")
            )
        ).fetchall()
        assert rows
        assert {r[1] for r in rows} == {"usr_local_pm"}, rows  # refs were rewritten
        assert all(str(r[2]).startswith("2026-01-01") for r in rows), rows

    async with session_factory() as session:
        t_ip = await session.get(Ticket, "t_ip")
        assert t_ip is not None and t_ip.status == "open"
        open_ids = set(
            (await session.execute(select(Ticket.ticket_id).where(Ticket.status == "open")))
            .scalars()
            .all()
        )
        assert "t_ip" in open_ids

    await engine.dispose()
