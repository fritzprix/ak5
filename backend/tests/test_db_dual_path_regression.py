"""Regression locks for the dual-DB / WAL board-wipe failure mode.

Incident summary (repeated in production):
1. Real data lived in project-local ``./ak5.db`` (e.g. 5 boards / 38 tickets).
2. Gateway always opened ``~/.local/share/ak5/ak5.db``.
3. Code only *warned* and continued — empty/seed global looked like boards vanished.
4. Copying the main DB while leaving a stale ``*-wal`` made SQLite replay the WAL
   and hide the restored rows again.

These tests must fail if anyone reintroduces warn-and-continue dual-DB behavior,
WAL-preserving copies, or serve/web starting with ``./ak5.db`` still present.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import ak5.paths as paths
import pytest
from ak5.cli.main import cli
from click.testing import CliRunner


def _reset_resolve_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "_resolved_local_db", False)


def _write_kanban_sqlite(path: Path, *, boards: int, tickets: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE boards (board_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE tickets (ticket_id TEXT PRIMARY KEY)")
        for i in range(boards):
            conn.execute("INSERT INTO boards(board_id) VALUES (?)", (f"board-{i}",))
        for i in range(tickets):
            conn.execute("INSERT INTO tickets(ticket_id) VALUES (?)", (f"TK-{i:03d}",))
        conn.commit()
    finally:
        conn.close()


def _setup_dual_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_boards: int,
    local_tickets: int,
    global_boards: int,
    global_tickets: int,
) -> tuple[Path, Path]:
    _reset_resolve_gate(monkeypatch)
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: cwd)
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    local_db = cwd / "ak5.db"
    global_db = app_data / "ak5.db"
    _write_kanban_sqlite(local_db, boards=local_boards, tickets=local_tickets)
    _write_kanban_sqlite(global_db, boards=global_boards, tickets=global_tickets)
    return local_db, global_db


def test_incident_rich_local_empty_global_refuses_auto_promote(tmp_path, monkeypatch):
    """Exact incident shape: local 5/38, global seed 1/0 → refuse (migrate-legacy only)."""
    local_db, global_db = _setup_dual_db(
        tmp_path,
        monkeypatch,
        local_boards=5,
        local_tickets=38,
        global_boards=1,
        global_tickets=0,
    )
    (global_db.parent / "ak5.db-wal").write_bytes(b"stale-empty-overlay")

    with pytest.raises(paths.DatabaseConflictError, match="migrate-legacy"):
        paths.resolve_legacy_cwd_db_conflict()

    assert local_db.exists(), "must not auto-delete richer local"
    assert paths.sqlite_board_ticket_counts(local_db) == (5, 38)
    assert paths.sqlite_board_ticket_counts(global_db) == (1, 0)

    # Explicit migrate completes the handoff and clears stale WAL.
    monkeypatch.chdir(tmp_path / "project")
    res = paths.migrate_legacy_cwd_data(global_db.parent, force=True, opt_in=True)
    assert res["db_migrated"] is True
    assert not local_db.exists()
    assert not (global_db.parent / "ak5.db-wal").exists()
    assert paths.sqlite_board_ticket_counts(global_db) == (5, 38)


def test_prepare_database_refuses_rich_local_without_migrate(tmp_path, monkeypatch):
    """Default path resolution must refuse dual-DB — not warn-and-continue or auto-promote."""
    from ak5.services import db_safety
    from ak5.services.db_safety import prepare_database_for_use

    local_db, global_db = _setup_dual_db(
        tmp_path,
        monkeypatch,
        local_boards=4,
        local_tickets=12,
        global_boards=1,
        global_tickets=0,
    )
    monkeypatch.setattr(db_safety, "_prepared", False)

    with pytest.raises(paths.DatabaseConflictError, match="Conflicting AK5 databases"):
        prepare_database_for_use(force=True)

    assert local_db.exists()
    assert paths.sqlite_board_ticket_counts(global_db) == (1, 0)


def test_replace_sqlite_never_keeps_destination_wal(tmp_path):
    """Copying main DB while leaving *-wal is the wipe mechanism — forbid it."""
    src = tmp_path / "good.db"
    dst = tmp_path / "dest.db"
    _write_kanban_sqlite(src, boards=5, tickets=38)
    _write_kanban_sqlite(dst, boards=1, tickets=0)
    wal = tmp_path / "dest.db-wal"
    shm = tmp_path / "dest.db-shm"
    wal.write_bytes(b"wal-that-would-hide-rows")
    shm.write_bytes(b"shm")

    paths.replace_sqlite_database(src, dst)

    assert paths.sqlite_board_ticket_counts(dst) == (5, 38)
    assert not wal.exists()
    assert not shm.exists()


def test_migrate_legacy_force_also_clears_target_wal(tmp_path, monkeypatch):
    _reset_resolve_gate(monkeypatch)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: cwd)
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    _write_kanban_sqlite(cwd / "ak5.db", boards=3, tickets=9)
    _write_kanban_sqlite(app_data / "ak5.db", boards=1, tickets=0)
    (app_data / "ak5.db-wal").write_bytes(b"stale")
    (cwd / "ak5.db-wal").write_bytes(b"local-wal")
    (cwd / "ak5.db-shm").write_bytes(b"local-shm")

    res = paths.migrate_legacy_cwd_data(app_data, force=True, opt_in=True)
    assert res["db_migrated"] is True
    assert paths.sqlite_board_ticket_counts(app_data / "ak5.db") == (3, 9)
    assert not (app_data / "ak5.db-wal").exists()
    assert not (cwd / "ak5.db").exists(), "migrate must remove cwd DB to end dual-DB"
    assert not (cwd / "ak5.db-wal").exists()
    assert not (cwd / "ak5.db-shm").exists()


def test_tickets_without_board_dominance_refuses_promote(tmp_path, monkeypatch):
    """local=1 board/5 tickets vs global=3 boards/0 tickets must conflict, not promote."""
    local_db, global_db = _setup_dual_db(
        tmp_path,
        monkeypatch,
        local_boards=1,
        local_tickets=5,
        global_boards=3,
        global_tickets=0,
    )

    with pytest.raises(paths.DatabaseConflictError, match="Conflicting AK5 databases"):
        paths.resolve_legacy_cwd_db_conflict()

    assert local_db.exists()
    assert paths.sqlite_board_ticket_counts(global_db) == (3, 0)


def test_incomparable_conflict_blocks_serve_cli(tmp_path, monkeypatch):
    """serve must exit non-zero — never print soft 'using global instead' and continue."""
    _setup_dual_db(
        tmp_path,
        monkeypatch,
        local_boards=2,
        local_tickets=10,
        global_boards=5,
        global_tickets=8,
    )
    monkeypatch.chdir(tmp_path / "project")

    runner = CliRunner()
    with patch("ak5.cli.commands.serve.uvicorn.run") as run_mock:
        result = runner.invoke(cli, ["serve", "--port", "18000"])

    assert result.exit_code == 1, result.output
    assert "Database conflict" in result.output or "Conflicting AK5 databases" in result.output
    assert "Using global DB instead" not in result.output
    run_mock.assert_not_called()


def test_incomparable_conflict_blocks_web_cli(tmp_path, monkeypatch):
    _setup_dual_db(
        tmp_path,
        monkeypatch,
        local_boards=2,
        local_tickets=10,
        global_boards=5,
        global_tickets=8,
    )
    monkeypatch.chdir(tmp_path / "project")

    runner = CliRunner()
    with (
        patch("ak5.cli.commands.web._load_dotenv_files"),
        patch("ak5.cli.commands.web.uvicorn.run") as run_mock,
    ):
        result = runner.invoke(cli, ["web", "--port", "18001", "--no-browser"])

    assert result.exit_code == 1, result.output
    assert "Database conflict" in result.output or "Conflicting AK5 databases" in result.output
    assert "ignoring; using global DB" not in result.output
    run_mock.assert_not_called()


def test_migrate_legacy_cli_runs_despite_dual_db_conflict(tmp_path, monkeypatch):
    """Importing the CLI must not run prepare — migrate-legacy is how users resolve conflicts."""
    local_db, global_db = _setup_dual_db(
        tmp_path,
        monkeypatch,
        local_boards=5,
        local_tickets=38,
        global_boards=1,
        global_tickets=0,
    )
    monkeypatch.chdir(tmp_path / "project")

    runner = CliRunner()
    # Help / group load must succeed even while dual-DB would block serve.
    help_result = runner.invoke(cli, ["migrate-legacy", "--help"])
    assert help_result.exit_code == 0, help_result.output

    result = runner.invoke(cli, ["migrate-legacy", "--force"])
    assert result.exit_code == 0, result.output
    assert not local_db.exists()
    assert paths.sqlite_board_ticket_counts(global_db) == (5, 38)


def test_serve_source_forbids_soft_ignore_dual_db():
    """Static guard: the old warn-and-continue copy must not return."""
    serve_src = Path(__file__).resolve().parents[1] / "src" / "ak5" / "cli" / "commands" / "serve.py"
    web_src = Path(__file__).resolve().parents[1] / "src" / "ak5" / "cli" / "commands" / "web.py"
    text = serve_src.read_text(encoding="utf-8") + "\n" + web_src.read_text(encoding="utf-8")

    assert "Using global DB instead" not in text
    assert "ignoring; using global DB" not in text
    assert "prepare_database_for_use" in text
    assert "DatabaseConflictError" in text
    assert "SystemExit(1)" in text


def test_paths_default_path_is_pure_prepare_owns_side_effects():
    """Static guard: Settings/path URL helpers must not mutate DBs; prepare does."""
    paths_src = Path(__file__).resolve().parents[1] / "src" / "ak5" / "paths.py"
    safety_src = Path(__file__).resolve().parents[1] / "src" / "ak5" / "services" / "db_safety.py"
    database_src = Path(__file__).resolve().parents[1] / "src" / "ak5" / "database.py"
    paths_text = paths_src.read_text(encoding="utf-8")
    safety_text = safety_src.read_text(encoding="utf-8")
    database_text = database_src.read_text(encoding="utf-8")

    default_fn = paths_text.split("def get_default_db_path", 1)[1].split("\ndef ", 1)[0]
    assert "resolve_legacy_cwd_db_conflict()" not in default_fn
    assert "migrate_legacy_cwd_data" not in default_fn
    assert "recover_live_db_if_wiped" not in default_fn
    assert "get_canonical_db_path" in default_fn

    assert "resolve_legacy_cwd_db_conflict()" in safety_text
    assert "recover_live_db_if_wiped" in safety_text
    assert "assert_database_url_matches_canonical" in safety_text
    # Importing ak5.database must NOT run prepare (blocks `ak5 migrate-legacy`).
    # Gate belongs in lifespan + serve/web/reset CLI, not module import.
    assert "prepare_database_for_use(settings.DATABASE_URL)" not in database_text
    assert "from ak5.services.db_safety import prepare_database_for_use" not in database_text
    main_text = (
        Path(__file__).resolve().parents[1] / "src" / "ak5" / "main.py"
    ).read_text(encoding="utf-8")
    assert "prepare_database_for_use" in main_text
    assert "def counts_dominate" in paths_text
    assert "counts_dominate(" in paths_text.split("def migrate_legacy_cwd_data", 1)[1]
