import sys
from pathlib import Path

from ak5.paths import (
    check_legacy_cwd_db,
    get_app_data_dir,
    get_default_database_url,
    migrate_legacy_cwd_data,
    warn_if_local_cwd_db_present,
)
from ak5.security import jwt_secret_file
from sqlalchemy.engine import make_url


def test_get_app_data_dir_env_override(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(custom_dir))

    resolved = get_app_data_dir()
    assert resolved == custom_dir.resolve()
    assert resolved.is_dir()


def test_get_app_data_dir_linux(tmp_path, monkeypatch):
    monkeypatch.delenv("AK5_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")

    # With XDG_DATA_HOME
    xdg_dir = tmp_path / "xdg_share"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_dir))
    resolved = get_app_data_dir()
    assert resolved == (xdg_dir / "ak5").resolve()

    # Without XDG_DATA_HOME -> fallback to ~/.local/share/ak5
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    fake_home = tmp_path / "fake_home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    resolved_fallback = get_app_data_dir()
    assert resolved_fallback == (fake_home / ".local" / "share" / "ak5").resolve()


def test_get_app_data_dir_darwin(tmp_path, monkeypatch):
    monkeypatch.delenv("AK5_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    fake_home = tmp_path / "fake_mac_home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    resolved = get_app_data_dir()
    assert resolved == (fake_home / "Library" / "Application Support" / "ak5").resolve()


def test_get_app_data_dir_windows(tmp_path, monkeypatch):
    monkeypatch.delenv("AK5_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    # With LOCALAPPDATA
    win_local = tmp_path / "win_localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(win_local))
    resolved = get_app_data_dir()
    assert resolved == (win_local / "ak5").resolve()

    # Fallback to AppData/Local without LOCALAPPDATA
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    fake_home = tmp_path / "fake_win_home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    resolved_fallback = get_app_data_dir()
    assert resolved_fallback == (fake_home / "AppData" / "Local" / "ak5").resolve()


def test_migrate_legacy_cwd_data_opt_in(tmp_path, monkeypatch):
    fake_cwd = tmp_path / "fake_cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)

    legacy_db = fake_cwd / "ak5.db"
    legacy_db.write_text("dummy-sqlite-data", encoding="utf-8")
    legacy_wal = fake_cwd / "ak5.db-wal"
    legacy_wal.write_text("wal-data", encoding="utf-8")

    legacy_attach_dir = fake_cwd / "data" / "attachments"
    legacy_attach_dir.mkdir(parents=True)
    (legacy_attach_dir / "test.txt").write_text("attachment-content", encoding="utf-8")

    target_dir = tmp_path / "app_data"
    target_dir.mkdir()

    # 1. Without opt-in, migration is skipped
    monkeypatch.delenv("AK5_MIGRATE_LEGACY", raising=False)
    res_skip = migrate_legacy_cwd_data(target_dir)
    assert not res_skip["db_migrated"]
    assert "Opt-in required" in res_skip["skipped_reason"]
    assert not (target_dir / "ak5.db").exists()

    # 2. With opt-in via CLI flag (opt_in=True), migration succeeds without env
    res_cli = migrate_legacy_cwd_data(target_dir, opt_in=True)
    assert res_cli["db_migrated"]
    assert res_cli["attachments_copied"] == 1

    migrated_db = target_dir / "ak5.db"
    assert migrated_db.is_file()
    assert migrated_db.read_text(encoding="utf-8") == "dummy-sqlite-data"

    # WAL is checkpointed away; stale target sidecars must not remain.
    assert not (target_dir / "ak5.db-wal").exists()
    assert not (target_dir / "ak5.db-shm").exists()
    assert not legacy_db.exists(), "migrate must remove cwd DB to end dual-DB"

    migrated_attach = target_dir / "attachments" / "test.txt"
    assert migrated_attach.is_file()
    assert migrated_attach.read_text(encoding="utf-8") == "attachment-content"

    # 3. Running again when target already exists without force -> skips DB overwrite
    legacy_db.write_text("modified-data", encoding="utf-8")
    res_exist = migrate_legacy_cwd_data(target_dir, opt_in=True)
    assert not res_exist["db_migrated"]
    assert "already exists" in res_exist["skipped_reason"]
    assert migrated_db.read_text(encoding="utf-8") == "dummy-sqlite-data"
    assert legacy_db.exists(), "skipped migrate must leave cwd DB in place"

    # 4. Env opt-in alone also works (fresh target)
    target_env = tmp_path / "app_data_env"
    target_env.mkdir()
    monkeypatch.setenv("AK5_MIGRATE_LEGACY", "1")
    res_env = migrate_legacy_cwd_data(target_env)
    assert res_env["db_migrated"]
    assert not legacy_db.exists()

    # 5. With force=True -> overwrites existing global DB
    legacy_db.write_text("modified-data", encoding="utf-8")
    res_force = migrate_legacy_cwd_data(target_dir, force=True, opt_in=True)
    assert res_force["db_migrated"]
    assert migrated_db.read_text(encoding="utf-8") == "modified-data"
    assert not legacy_db.exists()


def test_migrate_attachments_even_if_target_dir_exists(tmp_path, monkeypatch):
    fake_cwd = tmp_path / "fake_cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)

    legacy_attach = fake_cwd / "data" / "attachments"
    legacy_attach.mkdir(parents=True)
    (legacy_attach / "file1.txt").write_text("file1", encoding="utf-8")

    target_dir = tmp_path / "app_data"
    target_attachments = target_dir / "attachments"
    target_attachments.mkdir(parents=True)  # pre-existing attachments dir!

    res = migrate_legacy_cwd_data(target_dir, opt_in=True)
    assert res["attachments_copied"] == 1
    assert (target_attachments / "file1.txt").read_text(encoding="utf-8") == "file1"


def test_check_and_warn_local_cwd_db(tmp_path, monkeypatch):
    fake_cwd = tmp_path / "fake_cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)

    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert check_legacy_cwd_db() is None

    # Create local DB in cwd
    (fake_cwd / "ak5.db").write_text("local-db", encoding="utf-8")
    assert check_legacy_cwd_db() == fake_cwd / "ak5.db"

    # Warning does not raise
    warn_if_local_cwd_db_present()


def test_check_legacy_skips_when_database_url_is_cwd_db(tmp_path, monkeypatch):
    """DATABASE_URL=./ak5.db is intentional — not a legacy dual-DB conflict."""
    fake_cwd = tmp_path / "project"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)
    monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path / "global_app"))
    local = fake_cwd / "ak5.db"
    local.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{local.as_posix()}")

    assert check_legacy_cwd_db() is None


def test_check_legacy_skips_when_ak5_data_dir_is_cwd(tmp_path, monkeypatch):
    fake_cwd = tmp_path / "project"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)
    monkeypatch.setenv("AK5_DATA_DIR", str(fake_cwd))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (fake_cwd / "ak5.db").write_text("x", encoding="utf-8")
    assert check_legacy_cwd_db() is None


def test_get_default_database_url(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(custom_dir))

    url = get_default_database_url()
    assert url.startswith("sqlite+aiosqlite:///")
    parsed = make_url(url)
    assert Path(parsed.database) == (custom_dir / "ak5.db").resolve()


def test_jwt_secret_file_defaults_to_app_data_dir(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(custom_dir))
    monkeypatch.delenv("AK5_JWT_SECRET_FILE", raising=False)

    secret_path = jwt_secret_file()
    assert secret_path == custom_dir / "jwt_secret"


def _write_kanban_sqlite(path: Path, *, boards: int, tickets: int) -> None:
    import sqlite3

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


def test_resolve_refuses_richer_local_without_auto_promote(tmp_path, monkeypatch):
    """Richer local must not overwrite global on import/resolve — migrate-legacy only."""
    import ak5.paths as paths
    import pytest

    monkeypatch.setattr(paths, "_resolved_local_db", False)
    fake_cwd = tmp_path / "cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)

    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    local_db = fake_cwd / "ak5.db"
    global_db = app_data / "ak5.db"
    _write_kanban_sqlite(local_db, boards=5, tickets=38)
    _write_kanban_sqlite(global_db, boards=1, tickets=0)
    (app_data / "ak5.db-wal").write_bytes(b"stale")

    with pytest.raises(paths.DatabaseConflictError, match="migrate-legacy"):
        paths.resolve_legacy_cwd_db_conflict()
    assert local_db.exists()
    assert paths.sqlite_board_ticket_counts(local_db) == (5, 38)
    assert paths.sqlite_board_ticket_counts(global_db) == (1, 0)


def test_resolve_removes_stale_local_when_global_dominates(tmp_path, monkeypatch):
    import ak5.paths as paths

    monkeypatch.setattr(paths, "_resolved_local_db", False)
    fake_cwd = tmp_path / "cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    # Empty local stub (0/0) vs populated global — safe to delete stub only.
    _write_kanban_sqlite(fake_cwd / "ak5.db", boards=0, tickets=0)
    _write_kanban_sqlite(app_data / "ak5.db", boards=5, tickets=38)

    result = paths.resolve_legacy_cwd_db_conflict()
    assert result["action"] == "removed_stale_local"
    assert not (fake_cwd / "ak5.db").exists()
    assert paths.sqlite_board_ticket_counts(app_data / "ak5.db") == (5, 38)


def test_resolve_refuses_equal_counts_without_content_compare(tmp_path, monkeypatch):
    """Same counts ≠ same content — must not auto-delete either side."""
    import ak5.paths as paths
    import pytest

    monkeypatch.setattr(paths, "_resolved_local_db", False)
    fake_cwd = tmp_path / "cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    _write_kanban_sqlite(fake_cwd / "ak5.db", boards=2, tickets=5)
    _write_kanban_sqlite(app_data / "ak5.db", boards=2, tickets=5)

    with pytest.raises(paths.DatabaseConflictError, match="Conflicting AK5 databases"):
        paths.resolve_legacy_cwd_db_conflict()
    assert (fake_cwd / "ak5.db").exists()
    assert (app_data / "ak5.db").exists()


def test_resolve_refuses_seed_local_with_boards_but_no_tickets(tmp_path, monkeypatch):
    """Do not delete a local board graph just because ticket count is 0."""
    import ak5.paths as paths
    import pytest

    monkeypatch.setattr(paths, "_resolved_local_db", False)
    fake_cwd = tmp_path / "cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    _write_kanban_sqlite(fake_cwd / "ak5.db", boards=3, tickets=0)
    _write_kanban_sqlite(app_data / "ak5.db", boards=1, tickets=2)

    with pytest.raises(paths.DatabaseConflictError, match="Conflicting AK5 databases"):
        paths.resolve_legacy_cwd_db_conflict()
    assert paths.sqlite_board_ticket_counts(fake_cwd / "ak5.db") == (3, 0)


def test_resolve_raises_on_incomparable_both_populated(tmp_path, monkeypatch):
    import ak5.paths as paths
    import pytest

    monkeypatch.setattr(paths, "_resolved_local_db", False)
    fake_cwd = tmp_path / "cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    # Local has more tickets; global has more boards — neither dominates.
    _write_kanban_sqlite(fake_cwd / "ak5.db", boards=2, tickets=10)
    _write_kanban_sqlite(app_data / "ak5.db", boards=5, tickets=8)

    with pytest.raises(paths.DatabaseConflictError, match="Conflicting AK5 databases"):
        paths.resolve_legacy_cwd_db_conflict()
    assert (fake_cwd / "ak5.db").exists()
    assert (app_data / "ak5.db").exists()


def test_resolve_does_not_promote_when_local_tickets_but_fewer_boards(tmp_path, monkeypatch):
    """Review edge case: tickets-on-local vs boards-on-global must not auto-promote."""
    import ak5.paths as paths
    import pytest

    monkeypatch.setattr(paths, "_resolved_local_db", False)
    fake_cwd = tmp_path / "cwd"
    fake_cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)
    app_data = tmp_path / "app_data"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))

    _write_kanban_sqlite(fake_cwd / "ak5.db", boards=1, tickets=5)
    _write_kanban_sqlite(app_data / "ak5.db", boards=3, tickets=0)

    with pytest.raises(paths.DatabaseConflictError, match="Conflicting AK5 databases"):
        paths.resolve_legacy_cwd_db_conflict()
    assert paths.sqlite_board_ticket_counts(app_data / "ak5.db") == (3, 0)
    assert (fake_cwd / "ak5.db").exists()


def test_assert_database_url_matches_canonical(tmp_path, monkeypatch):
    import ak5.paths as paths
    import pytest

    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.delenv("AK5_ALLOW_DATABASE_URL_OVERRIDE", raising=False)

    canonical = paths.get_canonical_db_path()
    ok_url = f"sqlite+aiosqlite:///{canonical.as_posix()}"
    assert paths.assert_database_url_matches_canonical(ok_url) == canonical.resolve()

    other = tmp_path / "other.db"
    bad_url = f"sqlite+aiosqlite:///{other.as_posix()}"
    with pytest.raises(paths.DatabaseConflictError, match="one source of truth"):
        paths.assert_database_url_matches_canonical(bad_url)

    monkeypatch.setenv("AK5_ALLOW_DATABASE_URL_OVERRIDE", "1")
    assert paths.assert_database_url_matches_canonical(bad_url) == other.resolve()


def test_replace_sqlite_database_drops_stale_wal(tmp_path):
    import ak5.paths as paths

    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _write_kanban_sqlite(src, boards=3, tickets=7)
    _write_kanban_sqlite(dst, boards=1, tickets=0)
    (tmp_path / "dst.db-wal").write_bytes(b"corrupt-overlay")

    paths.replace_sqlite_database(src, dst)
    assert paths.sqlite_board_ticket_counts(dst) == (3, 7)
    assert not (tmp_path / "dst.db-wal").exists()


def test_replace_sqlite_backs_up_populated_destination(tmp_path, monkeypatch):
    import ak5.paths as paths

    monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path))
    src = tmp_path / "src.db"
    dst = tmp_path / "ak5.db"
    _write_kanban_sqlite(src, boards=5, tickets=38)
    _write_kanban_sqlite(dst, boards=2, tickets=9)

    bak = paths.replace_sqlite_database(src, dst)
    assert bak is not None and bak.is_file()
    assert paths.sqlite_board_ticket_counts(bak) == (2, 9)
    assert paths.sqlite_board_ticket_counts(dst) == (5, 38)


def test_replace_sqlite_refuses_when_source_wal_checkpoint_fails(tmp_path, monkeypatch):
    import ak5.paths as paths
    import pytest

    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _write_kanban_sqlite(src, boards=2, tickets=3)
    (tmp_path / "src.db-wal").write_bytes(b"unflushed")
    monkeypatch.setattr(paths, "_checkpoint_sqlite", lambda *_a, **_k: False)

    with pytest.raises((paths.SqliteReplaceError, RuntimeError), match="WAL|checkpoint"):
        paths.replace_sqlite_database(src, dst)
    assert not dst.exists() or paths.sqlite_board_ticket_counts(dst) != (2, 3)
