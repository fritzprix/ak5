"""Database safety: single source of truth, wipe detection, auto-backup/restore.

Production incidents repeatedly looked like \"boards vanished\" when:
1. Dual DB (cwd ./ak5.db vs global store) — handled in ``ak5.paths``.
2. Global file was replaced/reset to an empty seed (only ``proj-core-engine``,
   0 tickets) while richer backups still existed under ``backups/``.
3. Agents ran ``ak5 reset -y`` or an old PATH binary against the global store.

**Single entry point**: call :func:`prepare_database_for_use` from app lifespan
and CLI ``serve`` / ``web`` / ``reset`` before mutating or serving the store.
Do **not** call it at ``ak5.database`` import time — that blocks ``ak5 migrate-legacy``
when dual-DB conflict exists (the recovery path the error message recommends).
Do not sprinkle resolve/recover across Settings import paths.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ak5.paths import (
    get_app_data_dir,
    replace_sqlite_database,
    sqlite_board_ticket_counts,
)

logger = logging.getLogger(__name__)

_BACKUP_NAME_RE = re.compile(
    r"^(?:auto|local|global|wiped|manual).*\.db$",
    re.IGNORECASE,
)

_SKIP_AUTO_RESTORE_MARKER = ".ak5_skip_auto_restore"
_prepared = False


class DatabaseWipeError(RuntimeError):
    """Live DB looks wiped while a richer backup exists and auto-restore is disabled."""


def _backup_dir(app_dir: Path | None = None) -> Path:
    path = (app_dir or get_app_data_dir()) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def skip_auto_restore_marker(app_dir: Path | None = None) -> Path:
    """Marker written by ``ak5 reset`` so intentional empty seed is not undone."""
    return (app_dir or get_app_data_dir()) / _SKIP_AUTO_RESTORE_MARKER


def mark_skip_auto_restore(app_dir: Path | None = None) -> Path:
    """Record that the live DB was intentionally reset — do not auto-restore."""
    marker = skip_auto_restore_marker(app_dir)
    marker.write_text("intentional reset — refuse auto-restore from backups\n", encoding="utf-8")
    return marker


def clear_skip_auto_restore(app_dir: Path | None = None) -> None:
    marker = skip_auto_restore_marker(app_dir)
    if marker.exists():
        marker.unlink()


def list_sqlite_backups(app_dir: Path | None = None) -> list[Path]:
    """Return backup DB files newest-first (by mtime)."""
    bdir = _backup_dir(app_dir)
    files = [
        p
        for p in bdir.iterdir()
        if p.is_file() and p.suffix == ".db" and not p.name.endswith("-wal") and not p.name.endswith("-shm")
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def find_richest_backup(app_dir: Path | None = None) -> tuple[Path | None, tuple[int, int]]:
    """Return ``(path, (boards, tickets))`` for the backup with the most tickets (then boards)."""
    best: Path | None = None
    best_counts = (0, 0)
    for path in list_sqlite_backups(app_dir):
        boards, tickets = sqlite_board_ticket_counts(path)
        if tickets > best_counts[1] or (tickets == best_counts[1] and boards > best_counts[0]):
            best = path
            best_counts = (boards, tickets)
    return best, best_counts


def snapshot_live_database(live_db: Path | None = None, *, reason: str = "auto") -> Path | None:
    """If live DB has tickets, copy it into ``backups/`` before risky operations."""
    app_dir = get_app_data_dir()
    db = live_db or (app_dir / "ak5.db")
    boards, tickets = sqlite_board_ticket_counts(db)
    if tickets <= 0:
        return None
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "_", reason)[:32] or "auto"
    dest = _backup_dir(app_dir) / f"{safe_reason}.{ts}.db"
    replace_sqlite_database(db, dest)
    logger.info(
        "Snapshotted live DB (%d boards / %d tickets) → %s",
        boards,
        tickets,
        dest,
    )
    return dest


def _looks_like_wiped_seed(live_boards: int, live_tickets: int, *, live_exists: bool) -> bool:
    """True only for an on-disk seed/wipe shape — not a missing fresh-install file."""
    if not live_exists:
        return False
    if live_tickets > 0:
        return False
    # Fresh empty file / pre-seed: 0 boards — leave alone; seed will populate.
    # Classic wipe / post-seed empty: >=1 board (usually proj-core-engine) and 0 tickets.
    return live_boards >= 1


def recover_live_db_if_wiped(
    live_db: Path | None = None,
    *,
    auto_restore: bool | None = None,
) -> dict[str, Any]:
    """If live DB looks wiped but a richer backup exists, restore or refuse.

    Default ``auto_restore=True`` (override with ``AK5_AUTO_RESTORE_BACKUP=0``).

    Will **not** restore when:
    - live DB file is missing (brand-new install; stale ``backups/`` ignored)
    - live has 0 boards and 0 tickets (pre-seed empty — not a wipe)
    - ``.ak5_skip_auto_restore`` marker exists (written by ``ak5 reset``)
    """
    app_dir = get_app_data_dir()
    db = live_db or (app_dir / "ak5.db")
    live_exists = db.is_file()
    live_boards, live_tickets = sqlite_board_ticket_counts(db)
    result: dict[str, Any] = {
        "action": "none",
        "live_counts": (live_boards, live_tickets),
        "backup": None,
        "backup_counts": (0, 0),
    }

    if live_tickets > 0:
        # Real data again — clear intentional-reset marker if present.
        clear_skip_auto_restore(app_dir)
        return result

    if not _looks_like_wiped_seed(live_boards, live_tickets, live_exists=live_exists):
        return result

    if skip_auto_restore_marker(app_dir).is_file():
        logger.info(
            "Skip auto-restore: %s present (intentional reset). "
            "Delete the marker to allow restore from backups/.",
            skip_auto_restore_marker(app_dir),
        )
        result["action"] = "skipped_intentional_reset"
        return result

    backup, backup_counts = find_richest_backup(app_dir)
    result["backup"] = str(backup) if backup else None
    result["backup_counts"] = backup_counts
    backup_boards, backup_tickets = backup_counts

    if backup is None or backup_tickets <= 0:
        return result

    if auto_restore is None:
        auto_restore = os.environ.get("AK5_AUTO_RESTORE_BACKUP", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )

    if not auto_restore:
        raise DatabaseWipeError(
            f"Live database looks wiped ({live_boards} boards / {live_tickets} tickets) but "
            f"backup {backup} has {backup_boards} boards / {backup_tickets} tickets. "
            f"Restore manually or set AK5_AUTO_RESTORE_BACKUP=1."
        )

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    wiped_copy = _backup_dir(app_dir) / f"wiped.{ts}.db"
    if db.is_file():
        replace_sqlite_database(db, wiped_copy)
    replace_sqlite_database(backup, db)
    logger.warning(
        "Live DB was empty/seed (%d/%d); auto-restored from %s (%d/%d). "
        "Wiped copy kept at %s",
        live_boards,
        live_tickets,
        backup,
        backup_boards,
        backup_tickets,
        wiped_copy,
    )
    result["action"] = "restored_from_backup"
    result["wiped_copy"] = str(wiped_copy)
    return result


def prepare_database_for_use(database_url: str | None = None, *, force: bool = False) -> Path:
    """**The** DB safety gate: dual-DB reconcile → wipe recover → URL SOU check.

    Must run from lifespan / ``ak5 serve`` / ``ak5 web`` / ``ak5 reset`` before
    serving or wiping data. Idempotent within a process unless ``force=True``.
    Must **not** run at ``ak5.database`` import time (would block migrate-legacy).
    """
    global _prepared
    from ak5.paths import (
        assert_database_url_matches_canonical,
        get_canonical_db_path,
        migrate_legacy_cwd_data,
        resolve_legacy_cwd_db_conflict,
    )

    if _prepared and not force:
        live = get_canonical_db_path()
        if database_url is not None:
            assert_database_url_matches_canonical(database_url)
        return live

    app_dir = get_app_data_dir()
    if os.environ.get("AK5_MIGRATE_LEGACY", "").strip().lower() in ("1", "true", "yes"):
        migrate_legacy_cwd_data(app_dir, opt_in=True)
    else:
        resolve_legacy_cwd_db_conflict()

    live = get_canonical_db_path()
    recover_live_db_if_wiped(live)
    if database_url is not None:
        assert_database_url_matches_canonical(database_url)
    _prepared = True
    return live


def destructive_reset_allowed(*, yes_flag: bool, is_tty: bool) -> tuple[bool, str]:
    """Gate ``ak5 reset``. ``-y`` alone is not enough outside a TTY."""
    if is_tty and not yes_flag:
        return True, ""  # interactive confirm still required by caller
    if is_tty and yes_flag:
        return True, ""
    if os.environ.get("AK5_ALLOW_DESTRUCTIVE_RESET", "").strip().lower() in ("1", "true", "yes"):
        return True, ""
    return (
        False,
        "Non-interactive reset refused. Re-run in a TTY, or set "
        "AK5_ALLOW_DESTRUCTIVE_RESET=1 with --yes (CI only).",
    )
