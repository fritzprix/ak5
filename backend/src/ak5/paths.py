"""Cross-platform application data and storage paths for AK5."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_warned_local_db: bool = False


def get_app_data_dir(app_name: str = "ak5") -> Path:
    """Return OS-specific application data directory (cross-platform).

    Priority:
    1. AK5_DATA_DIR environment variable
    2. Windows: %LOCALAPPDATA%/ak5 (fallback: ~/.AppData/Local/ak5)
    3. macOS: ~/Library/Application Support/ak5
    4. Linux/Unix: $XDG_DATA_HOME/ak5 (fallback: ~/.local/share/ak5)
    """
    override = os.environ.get("AK5_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"

    app_dir = (base / app_name).expanduser().resolve()
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def check_legacy_cwd_db() -> Path | None:
    """Return Path to local cwd ak5.db if it exists and differs from global store."""
    local_db = Path.cwd() / "ak5.db"
    if not local_db.is_file():
        return None
    app_db = get_app_data_dir() / "ak5.db"
    try:
        if local_db.resolve() == app_db.resolve():
            return None
    except OSError:
        pass
    return local_db


def warn_if_local_cwd_db_present() -> None:
    """Emit a warning if a local ak5.db is found in cwd while using global store."""
    global _warned_local_db
    if _warned_local_db:
        return
    local_db = check_legacy_cwd_db()
    if local_db:
        target_db = get_app_data_dir() / "ak5.db"
        logger.warning(
            "Found local database at %s, but AK5 is using global database at %s. "
            "To use the local database, set DATABASE_URL=sqlite+aiosqlite:///./ak5.db. "
            "To migrate local data into global storage, run 'ak5 migrate-legacy' or set AK5_MIGRATE_LEGACY=1.",
            local_db,
            target_db,
        )
        _warned_local_db = True


def _copy_attachment_tree(src: Path, dst: Path) -> int:
    """Recursively copy files from src to dst without overwriting existing files."""
    copied = 0
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            target_file = dst / rel
            if not target_file.exists():
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target_file)
                copied += 1
    return copied


def _migrate_legacy_env_opt_in() -> bool:
    return os.environ.get("AK5_MIGRATE_LEGACY", "").strip().lower() in ("1", "true", "yes")


def migrate_legacy_cwd_data(
    target_dir: Path | None = None,
    *,
    force: bool = False,
    opt_in: bool | None = None,
) -> dict[str, Any]:
    """Migrate legacy cwd-based ak5.db and attachments to app data directory.

    Requires explicit opt-in: pass ``opt_in=True`` (CLI), or set ``AK5_MIGRATE_LEGACY=1``.
    ``force=True`` overwrites an existing global database; attachments always merge
    missing files only (never overwrite existing attachment files).
    """
    dest_dir = target_dir or get_app_data_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "db_migrated": False,
        "attachments_copied": 0,
        "skipped_reason": None,
    }

    allowed = _migrate_legacy_env_opt_in() if opt_in is None else opt_in
    if not allowed:
        result["skipped_reason"] = (
            "Opt-in required (run `ak5 migrate-legacy` or set AK5_MIGRATE_LEGACY=1)"
        )
        return result

    target_db = dest_dir / "ak5.db"
    legacy_db = Path.cwd() / "ak5.db"

    if legacy_db.is_file():
        try:
            if legacy_db.resolve() == target_db.resolve():
                result["skipped_reason"] = "Source and destination database are the same path"
            elif target_db.exists() and not force:
                result["skipped_reason"] = (
                    f"Global DB already exists at {target_db}. "
                    "Re-run with --force to overwrite."
                )
                logger.warning(result["skipped_reason"])
            else:
                shutil.copy2(legacy_db, target_db)
                result["db_migrated"] = True
                logger.info("Migrated legacy DB from %s to %s", legacy_db, target_db)
                for ext in ("-wal", "-shm"):
                    legacy_extra = Path.cwd() / f"ak5.db{ext}"
                    if legacy_extra.is_file():
                        shutil.copy2(legacy_extra, dest_dir / f"ak5.db{ext}")
        except OSError as e:
            logger.warning("Failed to migrate legacy db: %s", e)
            result["skipped_reason"] = str(e)

    # Attachments migration (merges missing files into target_attachments)
    target_attachments = dest_dir / "attachments"
    legacy_attachments = Path.cwd() / "data" / "attachments"
    if legacy_attachments.is_dir():
        try:
            if legacy_attachments.resolve() != target_attachments.resolve():
                copied_count = _copy_attachment_tree(legacy_attachments, target_attachments)
                result["attachments_copied"] = copied_count
                if copied_count > 0:
                    logger.info(
                        "Migrated %d attachments from %s to %s",
                        copied_count,
                        legacy_attachments,
                        target_attachments,
                    )
        except OSError as e:
            logger.warning("Failed to migrate legacy attachments: %s", e)

    return result


def get_default_db_path() -> Path:
    """Return default SQLite database file path in app local storage."""
    app_dir = get_app_data_dir()
    # Opt-in migration via environment only (CLI uses `ak5 migrate-legacy`)
    if _migrate_legacy_env_opt_in():
        migrate_legacy_cwd_data(app_dir, opt_in=True)
    else:
        warn_if_local_cwd_db_present()
    return app_dir / "ak5.db"


def get_default_database_url() -> str:
    """Return default SQLAlchemy SQLite URL pointing to app local storage db."""
    db_file = get_default_db_path()
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


def get_default_attachments_dir() -> Path:
    """Return default attachments directory in app local storage."""
    attachments = get_app_data_dir() / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    return attachments
