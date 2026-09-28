"""Regression locks for wiped-seed / backup auto-restore safety."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import ak5.services.db_safety as db_safety
import pytest


def _write_kanban_sqlite(path: Path, *, boards: int, tickets: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE boards (board_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE tickets (ticket_id TEXT PRIMARY KEY)")
        for i in range(boards):
            conn.execute("INSERT INTO boards(board_id) VALUES (?)", (f"b{i}",))
        for i in range(tickets):
            conn.execute("INSERT INTO tickets(ticket_id) VALUES (?)", (f"t{i}",))
        conn.commit()
    finally:
        conn.close()


def test_auto_restore_when_live_is_seed_and_backup_has_tickets(tmp_path, monkeypatch):
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.delenv("AK5_AUTO_RESTORE_BACKUP", raising=False)

    live = app_data / "ak5.db"
    _write_kanban_sqlite(live, boards=1, tickets=0)  # wiped/seed shape
    backup = app_data / "backups" / "global.good.db"
    _write_kanban_sqlite(backup, boards=5, tickets=38)

    result = db_safety.recover_live_db_if_wiped(live)
    assert result["action"] == "restored_from_backup"
    from ak5.paths import sqlite_board_ticket_counts

    assert sqlite_board_ticket_counts(live) == (5, 38)


def test_fresh_install_ignores_stale_backups(tmp_path, monkeypatch):
    """Missing live DB + old backups/ must NOT restore (brand-new install)."""
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.delenv("AK5_AUTO_RESTORE_BACKUP", raising=False)

    live = app_data / "ak5.db"
    assert not live.exists()
    _write_kanban_sqlite(app_data / "backups" / "old.db", boards=5, tickets=38)

    result = db_safety.recover_live_db_if_wiped(live)
    assert result["action"] == "none"
    assert not live.exists()


def test_intentional_reset_marker_blocks_auto_restore(tmp_path, monkeypatch):
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.delenv("AK5_AUTO_RESTORE_BACKUP", raising=False)

    live = app_data / "ak5.db"
    _write_kanban_sqlite(live, boards=1, tickets=0)
    _write_kanban_sqlite(app_data / "backups" / "good.db", boards=5, tickets=38)
    db_safety.mark_skip_auto_restore(app_data)

    result = db_safety.recover_live_db_if_wiped(live)
    assert result["action"] == "skipped_intentional_reset"
    from ak5.paths import sqlite_board_ticket_counts

    assert sqlite_board_ticket_counts(live) == (1, 0)


def test_refuse_wipe_when_auto_restore_disabled(tmp_path, monkeypatch):
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.setenv("AK5_AUTO_RESTORE_BACKUP", "0")

    live = app_data / "ak5.db"
    _write_kanban_sqlite(live, boards=1, tickets=0)
    _write_kanban_sqlite(app_data / "backups" / "good.db", boards=5, tickets=38)

    with pytest.raises(db_safety.DatabaseWipeError, match="looks wiped"):
        db_safety.recover_live_db_if_wiped(live)


def test_snapshot_skipped_when_empty(tmp_path, monkeypatch):
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    live = app_data / "ak5.db"
    _write_kanban_sqlite(live, boards=1, tickets=0)
    assert db_safety.snapshot_live_database(live) is None


def test_snapshot_when_populated(tmp_path, monkeypatch):
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    live = app_data / "ak5.db"
    _write_kanban_sqlite(live, boards=2, tickets=4)
    snap = db_safety.snapshot_live_database(live, reason="startup")
    assert snap is not None and snap.is_file()
    from ak5.paths import sqlite_board_ticket_counts

    assert sqlite_board_ticket_counts(snap) == (2, 4)


def test_noninteractive_reset_requires_env():
    ok, _ = db_safety.destructive_reset_allowed(yes_flag=True, is_tty=False)
    assert ok is False
    # with env
    import os

    os.environ["AK5_ALLOW_DESTRUCTIVE_RESET"] = "1"
    try:
        ok2, _ = db_safety.destructive_reset_allowed(yes_flag=True, is_tty=False)
        assert ok2 is True
    finally:
        del os.environ["AK5_ALLOW_DESTRUCTIVE_RESET"]


def test_destructive_reset_allowed_on_tty():
    ok, reason = db_safety.destructive_reset_allowed(yes_flag=False, is_tty=True)
    assert ok is True
    assert reason == ""
    ok2, _ = db_safety.destructive_reset_allowed(yes_flag=True, is_tty=True)
    assert ok2 is True


def test_empty_file_not_treated_as_wipe(tmp_path, monkeypatch):
    """0 boards / 0 tickets (pre-seed) must not restore from backups."""
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.delenv("AK5_AUTO_RESTORE_BACKUP", raising=False)

    live = app_data / "ak5.db"
    _write_kanban_sqlite(live, boards=0, tickets=0)
    _write_kanban_sqlite(app_data / "backups" / "old.db", boards=5, tickets=38)

    result = db_safety.recover_live_db_if_wiped(live)
    assert result["action"] == "none"
    from ak5.paths import sqlite_board_ticket_counts

    assert sqlite_board_ticket_counts(live) == (0, 0)


def test_populated_live_clears_skip_marker(tmp_path, monkeypatch):
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    live = app_data / "ak5.db"
    _write_kanban_sqlite(live, boards=2, tickets=3)
    db_safety.mark_skip_auto_restore(app_data)
    assert db_safety.skip_auto_restore_marker(app_data).is_file()

    result = db_safety.recover_live_db_if_wiped(live)
    assert result["action"] == "none"
    assert not db_safety.skip_auto_restore_marker(app_data).exists()


def test_prepare_database_idempotent(tmp_path, monkeypatch):
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.setattr(db_safety, "_prepared", False)
    url = f"sqlite+aiosqlite:///{(app_data / 'ak5.db').as_posix()}"

    live1 = db_safety.prepare_database_for_use(url, force=True)
    live2 = db_safety.prepare_database_for_use(url)  # no-op path
    assert live1 == live2
    assert db_safety._prepared is True


def test_prepare_opt_in_migrate_legacy(tmp_path, monkeypatch):
    import ak5.paths as paths

    monkeypatch.setattr(db_safety, "_prepared", False)
    monkeypatch.setattr(paths, "_resolved_local_db", False)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.setenv("AK5_MIGRATE_LEGACY", "1")
    monkeypatch.setattr(paths.Path, "cwd", lambda: cwd)

    _write_kanban_sqlite(cwd / "ak5.db", boards=2, tickets=7)
    live = db_safety.prepare_database_for_use(force=True)
    from ak5.paths import sqlite_board_ticket_counts

    assert live == app_data / "ak5.db"
    assert sqlite_board_ticket_counts(live) == (2, 7)
    assert not (cwd / "ak5.db").exists()
