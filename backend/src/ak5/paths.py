"""Cross-platform application data and storage paths for AK5."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_warned_local_db: bool = False
_resolved_local_db: bool = False


class DatabaseConflictError(RuntimeError):
    """Raised when cwd ./ak5.db and the global store both hold divergent real data."""


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
    """Return Path to cwd ``ak5.db`` if it is a *legacy* dual-DB copy — not the live store.

    Not legacy (returns ``None``) when:
    - cwd has no ``ak5.db``
    - cwd ``ak5.db`` is the canonical store (``AK5_DATA_DIR`` points at cwd)
    - ``DATABASE_URL`` explicitly opens that same file (intentional cwd store)
    """
    local_db = Path.cwd() / "ak5.db"
    if not local_db.is_file():
        return None
    try:
        local_resolved = local_db.resolve()
    except OSError:
        return local_db

    try:
        if local_resolved == get_canonical_db_path().resolve():
            return None
    except OSError:
        pass

    # Intentional: DATABASE_URL=sqlite+aiosqlite:///./ak5.db (or absolute path to cwd DB)
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        try:
            opened = sqlite_file_from_database_url(db_url)
        except Exception:
            opened = None
        if opened is not None and opened == local_resolved:
            return None

    return local_db


def counts_dominate(
    a_boards: int,
    a_tickets: int,
    b_boards: int,
    b_tickets: int,
) -> bool:
    """True when ``a`` strictly dominates ``b`` on both axes (tickets, then boards)."""
    return a_tickets >= b_tickets and a_boards >= b_boards and (
        a_tickets > b_tickets or a_boards > b_boards
    )


def sqlite_board_ticket_counts(db_path: Path) -> tuple[int, int]:
    """Return ``(boards, tickets)`` for a SQLite file. Missing/unreadable → ``(0, 0)``.

    Best-effort checkpoints first so unflushed WAL rows are included in the
    comparison used by conflict resolution. If the file is not writable, falls
    back to a read-only open (which still sees WAL when present beside the DB).
    """
    if not db_path.is_file():
        return (0, 0)
    _checkpoint_sqlite(db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            boards = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='boards'"
            ).fetchone()
            if not boards or boards[0] == 0:
                return (0, 0)
            board_count = int(conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0])
            ticket_count = int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
            return (board_count, ticket_count)
        finally:
            conn.close()
    except sqlite3.Error:
        return (0, 0)


def _is_sqlite_file(db_path: Path) -> bool:
    try:
        with db_path.open("rb") as handle:
            return handle.read(16).startswith(b"SQLite format 3")
    except OSError:
        return False


def _remove_sqlite_sidecars(db_path: Path) -> None:
    for ext in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{ext}")
        if sidecar.exists():
            sidecar.unlink()


def _checkpoint_sqlite(db_path: Path, *, strict: bool = False) -> bool:
    """Flush WAL into the main DB file. Return True on success / nothing to do.

    When ``strict`` is True and a ``*-wal`` sidecar exists, failure raises
    :class:`RuntimeError` so callers cannot copy a main file that is missing
    unflushed WAL rows.
    """
    if not db_path.is_file() or not _is_sqlite_file(db_path):
        return True
    wal_path = Path(f"{db_path}-wal")
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error as exc:
        if strict and wal_path.exists():
            raise RuntimeError(
                f"Cannot safely copy SQLite DB {db_path}: WAL checkpoint failed "
                f"while {wal_path.name} exists ({exc}). Close other AK5 processes "
                f"and retry."
            ) from exc
        logger.warning("Could not checkpoint SQLite DB %s: %s", db_path, exc)
        return False


class SqliteReplaceError(RuntimeError):
    """Raised when a DB file cannot be replaced without risking silent data loss."""


def replace_sqlite_database(source: Path, destination: Path, *, backup_destination: bool = True) -> Path | None:
    """Copy ``source`` over ``destination``, discarding stale WAL/SHM overlays.

    Copying only the main file while leaving an old ``*-wal`` in place makes
    SQLite replay the WAL and appear to "lose" boards — the failure mode that
    wiped AK5 boards on restart.

    When ``backup_destination`` is true and the destination already holds boards
    or tickets, a copy is written under ``<dest_dir>/backups/pre-replace.*.db``
    before overwrite.

    If ``source`` has a ``*-wal`` sidecar, checkpoint **must** succeed or this
    raises :class:`SqliteReplaceError` / :class:`RuntimeError`.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    bak_path: Path | None = None
    if backup_destination and destination.is_file():
        boards, tickets = sqlite_board_ticket_counts(destination)
        if boards > 0 or tickets > 0:
            from datetime import UTC, datetime

            bak_dir = destination.parent / "backups"
            bak_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            bak_path = bak_dir / f"pre-replace.{ts}.db"
            _checkpoint_sqlite(destination, strict=False)
            shutil.copy2(destination, bak_path)
            logger.info(
                "Backed up destination DB (%d boards / %d tickets) → %s before replace",
                boards,
                tickets,
                bak_path,
            )

    source_wal = Path(f"{source}-wal")
    if not _checkpoint_sqlite(source, strict=source_wal.exists()) and source_wal.exists():
        raise SqliteReplaceError(
            f"Refusing to copy {source}: checkpoint failed and WAL still present"
        )
    _remove_sqlite_sidecars(destination)
    shutil.copy2(source, destination)
    _remove_sqlite_sidecars(destination)
    return bak_path


def _remove_cwd_db_files(local_db: Path) -> None:
    for path in (local_db, Path(f"{local_db}-wal"), Path(f"{local_db}-shm")):
        if path.exists():
            path.unlink()


def get_canonical_db_path() -> Path:
    """Return the single on-disk SQLite path for this process (``AK5_DATA_DIR/ak5.db``).

    Pure path helper — no migrate / promote / delete side effects.
    """
    return get_app_data_dir() / "ak5.db"


def sqlite_file_from_database_url(database_url: str) -> Path | None:
    """Return the SQLite file path for a SQLAlchemy URL, or ``None`` for ``:memory:``."""
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def assert_database_url_matches_canonical(database_url: str) -> Path | None:
    """Refuse a ``DATABASE_URL`` that opens a different file than the canonical store.

    One source of truth: ``AK5_DATA_DIR/ak5.db``. A divergent ``DATABASE_URL`` is how
    Docker/harness/env misconfig opens an empty seed while real boards sit elsewhere.

    Override (tests / intentional ephemeral DBs only): ``AK5_ALLOW_DATABASE_URL_OVERRIDE=1``.
    """
    if os.environ.get("AK5_ALLOW_DATABASE_URL_OVERRIDE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return sqlite_file_from_database_url(database_url)

    opened = sqlite_file_from_database_url(database_url)
    if opened is None:
        return None

    canonical = get_canonical_db_path().resolve()
    if opened != canonical:
        raise DatabaseConflictError(
            "DATABASE_URL does not match the canonical AK5 store (one source of truth).\n"
            f"  DATABASE_URL file: {opened}\n"
            f"  canonical store:   {canonical}\n"
            "Fix: unset DATABASE_URL (preferred), or set AK5_DATA_DIR so both paths match.\n"
            "Tests only: AK5_ALLOW_DATABASE_URL_OVERRIDE=1"
        )
    return opened


def resolve_legacy_cwd_db_conflict() -> dict[str, Any]:
    """Reconcile cwd ``./ak5.db`` with the global store before the server opens it.

    Policy (refuse ambiguity — never heuristic-promote or delete real structure):
    - No local file → no-op
    - Local empty (0 boards and 0 tickets) → delete empty cwd stub only
    - Local has any boards or tickets → raise :class:`DatabaseConflictError`
      (explicit ``ak5 migrate-legacy`` / delete ``./ak5.db``)
    """
    global _resolved_local_db
    result: dict[str, Any] = {
        "action": "none",
        "local": None,
        "global": None,
        "local_counts": (0, 0),
        "global_counts": (0, 0),
    }
    if _resolved_local_db:
        return result

    local_db = check_legacy_cwd_db()
    if not local_db:
        _resolved_local_db = True
        return result

    global_db = get_canonical_db_path()
    local_counts = sqlite_board_ticket_counts(local_db)
    global_counts = sqlite_board_ticket_counts(global_db)
    local_boards, local_tickets = local_counts
    global_boards, global_tickets = global_counts

    result.update(
        {
            "local": str(local_db),
            "global": str(global_db),
            "local_counts": local_counts,
            "global_counts": global_counts,
        }
    )

    local_empty = local_boards == 0 and local_tickets == 0
    if local_empty:
        logger.warning(
            "Removing empty local DB stub %s; keeping canonical %s (%d boards / %d tickets).",
            local_db,
            global_db,
            global_boards,
            global_tickets,
        )
        _remove_cwd_db_files(local_db)
        result["action"] = "removed_stale_local"
        _resolved_local_db = True
        return result

    message = (
        f"Conflicting AK5 databases refuse to start silently.\n"
        f"  local : {local_db} → {local_boards} boards / {local_tickets} tickets\n"
        f"  global: {global_db} → {global_boards} boards / {global_tickets} tickets\n"
        f"Keep local → run: ak5 migrate-legacy --force\n"
        f"Keep global → delete: {local_db}"
    )
    raise DatabaseConflictError(message)


def warn_if_local_cwd_db_present() -> None:
    """Deprecated soft warning; prefer :func:`resolve_legacy_cwd_db_conflict`."""
    global _warned_local_db
    if _warned_local_db:
        return
    local_db = check_legacy_cwd_db()
    if local_db:
        target_db = get_app_data_dir() / "ak5.db"
        logger.warning(
            "Found local database at %s, but AK5 is using global database at %s. "
            "Conflicts are now auto-resolved or refuse to start; "
            "prefer removing ./ak5.db or running `ak5 migrate-legacy --force`.",
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
            else:
                # If global is ticket-empty and local dominates, promote even without --force.
                lb, lt = sqlite_board_ticket_counts(legacy_db)
                gb, gt = sqlite_board_ticket_counts(target_db) if target_db.exists() else (0, 0)
                local_dominates = counts_dominate(lb, lt, gb, gt)
                if target_db.exists() and not force and not (gt == 0 and local_dominates):
                    result["skipped_reason"] = (
                        f"Global DB already exists at {target_db}. "
                        "Re-run with --force to overwrite."
                    )
                    logger.warning(result["skipped_reason"])
                else:
                    # Checkpoint + drop target WAL so a stale *-wal cannot hide migrated rows.
                    replace_sqlite_database(legacy_db, target_db)
                    # Complete the handoff: remove cwd DB so dual-DB cannot recur.
                    _remove_cwd_db_files(legacy_db)
                    result["db_migrated"] = True
                    logger.info("Migrated legacy DB from %s to %s (cwd copy removed)", legacy_db, target_db)
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
    """Return the canonical SQLite path (``AK5_DATA_DIR/ak5.db``).

    Side-effect free: does **not** migrate, promote, delete, or auto-restore.
    Call :func:`ak5.services.db_safety.prepare_database_for_use` from serve/web/lifespan
    before opening the engine for real work.
    """
    return get_canonical_db_path()


def get_default_database_url() -> str:
    """Return default SQLAlchemy SQLite URL pointing to the canonical store."""
    db_file = get_default_db_path()
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


def get_default_attachments_dir() -> Path:
    """Return default attachments directory in app local storage."""
    attachments = get_app_data_dir() / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    return attachments
