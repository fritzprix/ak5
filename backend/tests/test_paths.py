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

    migrated_wal = target_dir / "ak5.db-wal"
    assert migrated_wal.is_file()
    assert migrated_wal.read_text(encoding="utf-8") == "wal-data"

    migrated_attach = target_dir / "attachments" / "test.txt"
    assert migrated_attach.is_file()
    assert migrated_attach.read_text(encoding="utf-8") == "attachment-content"

    # 3. Running again when target already exists without force -> skips DB overwrite
    legacy_db.write_text("modified-data", encoding="utf-8")
    res_exist = migrate_legacy_cwd_data(target_dir, opt_in=True)
    assert not res_exist["db_migrated"]
    assert "already exists" in res_exist["skipped_reason"]
    assert migrated_db.read_text(encoding="utf-8") == "dummy-sqlite-data"

    # 4. Env opt-in alone also works (fresh target)
    target_env = tmp_path / "app_data_env"
    target_env.mkdir()
    monkeypatch.setenv("AK5_MIGRATE_LEGACY", "1")
    res_env = migrate_legacy_cwd_data(target_env)
    assert res_env["db_migrated"]

    # 5. With force=True -> overwrites existing global DB
    res_force = migrate_legacy_cwd_data(target_dir, force=True, opt_in=True)
    assert res_force["db_migrated"]
    assert migrated_db.read_text(encoding="utf-8") == "modified-data"


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

    assert check_legacy_cwd_db() is None

    # Create local DB in cwd
    (fake_cwd / "ak5.db").write_text("local-db", encoding="utf-8")
    assert check_legacy_cwd_db() == fake_cwd / "ak5.db"

    # Warning does not raise
    warn_if_local_cwd_db_present()


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
