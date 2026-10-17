import json
import logging
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ak5.config import settings
from ak5.paths import get_app_data_dir

logger = logging.getLogger(__name__)

# Fixed export shape for #13 daemon.toml import (do not rename keys).
LEGACY_SUBSCRIPTIONS_EXPORT_KEYS = (
    "subscription_id",
    "board_id",
    "events",
    "for_agent",
    "exec_command",
    "created_by",
)

# Do NOT call prepare_database_for_use here. Importing ak5.database (via CLI
# reset → seed) must not refuse dual-DB before `ak5 migrate-legacy` can run.
# Safety gate lives in lifespan + `ak5 serve` / `ak5 web` (see main.py).

# Configure SQLite async engine with WAL mode and busy_timeout
connect_args = {}
if "sqlite" in settings.DATABASE_URL:
    connect_args["timeout"] = settings.SQLITE_BUSY_TIMEOUT / 1000.0
    try:
        url = make_url(settings.DATABASE_URL)
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as e:
        logger.warning(
            "Failed to create SQLite database parent directory for %s: %s",
            settings.DATABASE_URL,
            e,
        )

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    future=True,
)

# Apply SQLite pragmas for WAL mode, busy timeout, and foreign key enforcement
if "sqlite" in settings.DATABASE_URL:

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute(f"PRAGMA busy_timeout={settings.SQLITE_BUSY_TIMEOUT};")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


def _sqlite_blocked_by_has_ticket_fk(sync_conn: Connection) -> bool:
    """True when tickets.blocked_by still references tickets.ticket_id."""
    rows = sync_conn.execute(text("PRAGMA foreign_key_list(tickets)")).mappings().all()
    return any(row["from"] == "blocked_by" and row["table"] == "tickets" for row in rows)


def migrate_sqlite_blocked_by_to_text(sync_conn: Connection) -> None:
    """Rewrite tickets so blocked_by is free-text (drops legacy ticket self-FK).

    Older schemas treated blocked_by as a dependency ticket id. The product uses it
    as a human/agent block reason, so the FK must not remain.

    Caller must disable foreign_keys outside any transaction before DROP/rebuild
    (see ``_run_sqlite_table_rebuild``).
    """
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets'")
    ).scalar()
    if not exists:
        return
    if not _sqlite_blocked_by_has_ticket_fk(sync_conn):
        return

    sync_conn.execute(
        text(
            """
            CREATE TABLE tickets__mig_blocked_by (
                ticket_id VARCHAR(64) NOT NULL PRIMARY KEY,
                board_id VARCHAR(128) NOT NULL,
                column_id VARCHAR(128) NOT NULL,
                parent_ticket_id VARCHAR(64),
                title VARCHAR(256) NOT NULL,
                description TEXT,
                priority VARCHAR(16) NOT NULL,
                rank VARCHAR(128) NOT NULL,
                labels TEXT NOT NULL,
                assigned_to VARCHAR(128),
                created_by VARCHAR(128) NOT NULL,
                status VARCHAR(16) NOT NULL,
                blocked_by TEXT,
                execution_context TEXT,
                due_date DATETIME,
                is_archived BOOLEAN NOT NULL DEFAULT 0,
                archived_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(board_id) REFERENCES boards (board_id) ON DELETE CASCADE,
                FOREIGN KEY(column_id) REFERENCES columns (column_id),
                FOREIGN KEY(parent_ticket_id) REFERENCES tickets__mig_blocked_by (ticket_id)
                    ON DELETE SET NULL,
                FOREIGN KEY(assigned_to) REFERENCES actors (actor_id) ON DELETE SET NULL,
                FOREIGN KEY(created_by) REFERENCES actors (actor_id),
                CONSTRAINT check_ticket_priority
                    CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
                CONSTRAINT check_ticket_status
                    CHECK (status IN ('open', 'in_progress', 'blocked', 'done'))
            )
            """
        )
    )
    t_cols = {row["name"] for row in sync_conn.execute(text("PRAGMA table_info(tickets)")).mappings().all()}
    arc_col = "is_archived" if "is_archived" in t_cols else "0 AS is_archived"
    arc_at_col = "archived_at" if "archived_at" in t_cols else "NULL AS archived_at"

    sync_conn.execute(
        text(
            f"""
            INSERT INTO tickets__mig_blocked_by (
                ticket_id, board_id, column_id, parent_ticket_id, title, description,
                priority, rank, labels, assigned_to, created_by, status, blocked_by,
                execution_context, due_date, is_archived, archived_at, created_at, updated_at
            )
            SELECT
                ticket_id, board_id, column_id, parent_ticket_id, title, description,
                priority, rank, labels, assigned_to, created_by, status, blocked_by,
                execution_context, due_date, {arc_col}, {arc_at_col}, created_at, updated_at
            FROM tickets
            """
        )
    )
    sync_conn.execute(text("DROP TABLE tickets"))
    sync_conn.execute(text("ALTER TABLE tickets__mig_blocked_by RENAME TO tickets"))
    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tickets_board ON tickets (board_id)"))
    sync_conn.execute(
        text("CREATE INDEX IF NOT EXISTS idx_tickets_column_rank ON tickets (column_id, rank)")
    )
    sync_conn.execute(
        text("CREATE INDEX IF NOT EXISTS idx_tickets_assigned ON tickets (assigned_to)")
    )
    sync_conn.execute(
        text("CREATE INDEX IF NOT EXISTS idx_tickets_parent ON tickets (parent_ticket_id)")
    )


def migrate_sqlite_archive_fields(sync_conn: Connection) -> None:
    """Ensure is_archived and archived_at columns exist on tickets table."""
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets'")
    ).scalar()
    if not exists:
        return
    rows = sync_conn.execute(text("PRAGMA table_info(tickets)")).mappings().all()
    col_names = {row["name"] for row in rows}
    if "is_archived" not in col_names:
        sync_conn.execute(text("ALTER TABLE tickets ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0"))
        sync_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tickets_archived ON tickets (is_archived)"))
    if "archived_at" not in col_names:
        sync_conn.execute(text("ALTER TABLE tickets ADD COLUMN archived_at DATETIME"))


def _table_column_names(sync_conn: Connection, table: str) -> set[str]:
    rows = sync_conn.execute(text(f"PRAGMA table_info({table})")).mappings().all()
    return {row["name"] for row in rows}


def _add_column_if_missing(
    sync_conn: Connection,
    table: str,
    column: str,
    ddl_type: str,
) -> None:
    cols = _table_column_names(sync_conn, table)
    if column not in cols:
        sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def migrate_sqlite_board_workflow_fields(sync_conn: Connection) -> None:
    """Add boards.kind / template / auto_created (workflow.md §9)."""
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='boards'")
    ).scalar()
    if not exists:
        return
    _add_column_if_missing(sync_conn, "boards", "kind", "VARCHAR(16) NOT NULL DEFAULT 'standard'")
    _add_column_if_missing(sync_conn, "boards", "template", "VARCHAR(64)")
    _add_column_if_missing(sync_conn, "boards", "auto_created", "BOOLEAN NOT NULL DEFAULT 0")
    # Ensure inbox board kind if already present (seed may run later).
    sync_conn.execute(
        text("UPDATE boards SET kind = 'inbox' WHERE board_id = 'inbox' AND (kind IS NULL OR kind = '')")
    )


def migrate_sqlite_ticket_workflow_fields(sync_conn: Connection) -> None:
    """Add ticket claim / blocked / attempt columns (workflow.md §9)."""
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets'")
    ).scalar()
    if not exists:
        return
    _add_column_if_missing(sync_conn, "tickets", "original_request", "TEXT")
    _add_column_if_missing(sync_conn, "tickets", "attempt", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(sync_conn, "tickets", "max_attempts", "INTEGER NOT NULL DEFAULT 3")
    _add_column_if_missing(sync_conn, "tickets", "claim_token", "VARCHAR(128)")
    _add_column_if_missing(sync_conn, "tickets", "claimed_by", "VARCHAR(128)")
    _add_column_if_missing(sync_conn, "tickets", "lease_expires_at", "DATETIME")
    _add_column_if_missing(sync_conn, "tickets", "lease_expirations", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(sync_conn, "tickets", "submitted_by", "VARCHAR(128)")
    _add_column_if_missing(sync_conn, "tickets", "blocked_kind", "VARCHAR(32)")
    _add_column_if_missing(sync_conn, "tickets", "blocked_since", "DATETIME")
    _add_column_if_missing(sync_conn, "tickets", "blocked_actor", "VARCHAR(128)")


def migrate_sqlite_subscription_add_fields(sync_conn: Connection) -> None:
    """Add subscription ack/consumer/filter columns (events.md §2). Non-destructive."""
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscriptions'")
    ).scalar()
    if not exists:
        return

    _add_column_if_missing(sync_conn, "subscriptions", "ack_cursor", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(sync_conn, "subscriptions", "consumer_id", "VARCHAR(128)")
    _add_column_if_missing(
        sync_conn, "subscriptions", "consumer_lease_expires_at", "DATETIME"
    )
    _add_column_if_missing(sync_conn, "subscriptions", "role", "VARCHAR(64)")
    _add_column_if_missing(sync_conn, "subscriptions", "capabilities", "TEXT")


def migrate_sqlite_subscription_nullable_exec(sync_conn: Connection) -> None:
    """Legacy alias — prefer :func:`migrate_sqlite_subscription_filter_only`."""
    migrate_sqlite_subscription_filter_only(sync_conn)


def export_legacy_subscriptions_json(
    sync_conn: Connection,
    src_cols: set[str],
    *,
    export_path: Path | None = None,
) -> Path | None:
    """Export v1 exec subscriptions before dropping ``exec_command``.

    Writes ``AK5_DATA_DIR/legacy_subscriptions.json`` with a fixed key set so
    issue #13's daemon can import hooks into ``daemon.toml``.
    """
    if "exec_command" not in src_cols:
        return None

    def _col(name: str) -> str:
        return name if name in src_cols else f"NULL AS {name}"

    rows = sync_conn.execute(
        text(
            f"""
            SELECT
                subscription_id,
                {_col("board_id")},
                {_col("events")},
                {_col("for_agent")},
                exec_command,
                {_col("created_by")}
            FROM subscriptions
            """
        )
    ).mappings().all()

    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append({k: row.get(k) for k in LEGACY_SUBSCRIPTIONS_EXPORT_KEYS})

    path = export_path or (get_app_data_dir() / "legacy_subscriptions.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.warning(
        "Exported %d v1 exec subscription(s) to %s before dropping exec_command "
        "(import into daemon.toml via #13)",
        len(payload),
        path,
    )
    return path


def migrate_sqlite_subscription_filter_only(sync_conn: Connection) -> None:
    """Rebuild subscriptions as filter-only: drop server exec_command (events.md §2).

    Caller must disable foreign_keys outside any transaction before DROP/rebuild.
    Before dropping ``exec_command``, exports hooks to ``legacy_subscriptions.json``.
    Migrated rows get ``ack_cursor = max(events.seq)`` to avoid wake storms.
    """
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscriptions'")
    ).scalar()
    if not exists:
        return

    src_cols = _table_column_names(sync_conn, "subscriptions")
    needs_rebuild = (
        "exec_command" in src_cols
        or "ignore_actor" in src_cols
        or "debounce_seconds" in src_cols
        or "role" not in src_cols
        or "capabilities" not in src_cols
    )
    if not needs_rebuild:
        return

    export_legacy_subscriptions_json(sync_conn, src_cols)

    # Tip of the event log — skip retention history for migrated subscriptions.
    events_exist = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'")
    ).scalar()
    if events_exist:
        max_seq = int(
            sync_conn.execute(text("SELECT COALESCE(MAX(seq), 0) FROM events")).scalar() or 0
        )
    else:
        max_seq = 0

    sync_conn.execute(
        text(
            """
            CREATE TABLE subscriptions__mig (
                subscription_id VARCHAR(128) NOT NULL PRIMARY KEY,
                board_id VARCHAR(128),
                events TEXT,
                role VARCHAR(64),
                capabilities TEXT,
                for_agent VARCHAR(128),
                created_by VARCHAR(128) NOT NULL DEFAULT 'system',
                created_at DATETIME NOT NULL,
                ack_cursor INTEGER NOT NULL DEFAULT 0,
                consumer_id VARCHAR(128),
                consumer_lease_expires_at DATETIME
            )
            """
        )
    )
    select_events = "events" if "events" in src_cols else "NULL AS events"
    select_role = "role" if "role" in src_cols else "NULL AS role"
    select_caps = "capabilities" if "capabilities" in src_cols else "NULL AS capabilities"
    select_for = "for_agent" if "for_agent" in src_cols else "NULL AS for_agent"
    # Always start migrated subscriptions at the current tip (not historical 0).
    select_ack = f"{max_seq} AS ack_cursor"
    select_consumer = "consumer_id" if "consumer_id" in src_cols else "NULL AS consumer_id"
    select_lease = (
        "consumer_lease_expires_at"
        if "consumer_lease_expires_at" in src_cols
        else "NULL AS consumer_lease_expires_at"
    )
    sync_conn.execute(
        text(
            f"""
            INSERT INTO subscriptions__mig (
                subscription_id, board_id, events, role, capabilities, for_agent,
                created_by, created_at,
                ack_cursor, consumer_id, consumer_lease_expires_at
            )
            SELECT
                subscription_id, board_id, {select_events}, {select_role}, {select_caps},
                {select_for}, created_by, created_at,
                {select_ack}, {select_consumer}, {select_lease}
            FROM subscriptions
            """
        )
    )
    sync_conn.execute(text("DROP TABLE subscriptions"))
    sync_conn.execute(text("ALTER TABLE subscriptions__mig RENAME TO subscriptions"))
    sync_conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_subscriptions_board_id ON subscriptions (board_id)")
    )


def migrate_sqlite_subscription_workflow_fields(sync_conn: Connection) -> None:
    """Add subscription ack/consumer/filter fields; drop server exec (events.md §2).

    When used inside ``engine.begin()``, the rebuild half is a no-op for FK
    enforcement — prefer ``run_sqlite_schema_migrations`` which splits ADD vs rebuild.
    """
    migrate_sqlite_subscription_add_fields(sync_conn)
    migrate_sqlite_subscription_filter_only(sync_conn)


def _column_stage_check_sql(sync_conn: Connection) -> str | None:
    """Return the SQL of check_column_stage if present."""
    rows = sync_conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='columns'")
    ).fetchall()
    if not rows or not rows[0][0]:
        return None
    return str(rows[0][0])


def migrate_sqlite_column_stages(sync_conn: Connection) -> None:
    """Expand column stage CHECK to 7 stages; map review→agent_review, open→backlog.

    Caller must disable foreign_keys outside any transaction before DROP/rebuild
    (see ``_run_sqlite_table_rebuild``).
    """
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='columns'")
    ).scalar()
    if not exists:
        return

    table_sql = _column_stage_check_sql(sync_conn) or ""
    needs_rebuild = (
        "agent_review" not in table_sql
        or "unclassified" not in table_sql
        or "'open'" in table_sql
        or "'review'" in table_sql
    )

    # Remap only when the new CHECK already allows the target values.
    if not needs_rebuild:
        sync_conn.execute(text("UPDATE columns SET stage = 'agent_review' WHERE stage = 'review'"))
        sync_conn.execute(text("UPDATE columns SET stage = 'backlog' WHERE stage = 'open'"))
        return

    # Rebuild with remapping in the INSERT — old CHECK rejects in-place UPDATEs.
    sync_conn.execute(
        text(
            """
            CREATE TABLE columns__mig_stages (
                column_id VARCHAR(128) NOT NULL PRIMARY KEY,
                board_id VARCHAR(128) NOT NULL,
                name VARCHAR(128) NOT NULL,
                stage VARCHAR(32) NOT NULL,
                position INTEGER NOT NULL,
                wip_limit INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(board_id) REFERENCES boards (board_id) ON DELETE CASCADE,
                CONSTRAINT check_column_stage CHECK (
                    stage IN (
                        'unclassified', 'backlog', 'in_progress', 'agent_review',
                        'postmortem_pending', 'human_review', 'done'
                    )
                )
            )
            """
        )
    )
    # columns may lack updated_at depending on vintage.
    col_names = _table_column_names(sync_conn, "columns")
    created = "created_at" if "created_at" in col_names else "CURRENT_TIMESTAMP AS created_at"
    sync_conn.execute(
        text(
            f"""
            INSERT INTO columns__mig_stages (
                column_id, board_id, name, stage, position, wip_limit, created_at
            )
            SELECT
                column_id,
                board_id,
                name,
                CASE stage
                    WHEN 'review' THEN 'agent_review'
                    WHEN 'open' THEN 'backlog'
                    ELSE stage
                END,
                position,
                wip_limit,
                {created}
            FROM columns
            """
        )
    )
    sync_conn.execute(text("DROP TABLE columns"))
    sync_conn.execute(text("ALTER TABLE columns__mig_stages RENAME TO columns"))


async def _run_sqlite_table_rebuild(
    async_engine: AsyncEngine,
    migrate_fn: Callable[[Connection], None],
) -> None:
    """Run a DROP/rebuild migration with FK enforcement disabled.

    SQLite ignores ``PRAGMA foreign_keys`` inside an open transaction, so
    ``engine.begin()`` makes OFF a no-op and ``DROP TABLE columns`` fails when
    tickets still reference it. Disable FK on the connection, commit that
    pragma change, then BEGIN the rebuild and verify with foreign_key_check.
    """
    async with async_engine.connect() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.commit()
        async with conn.begin():
            await conn.run_sync(migrate_fn)
            bad = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall()
            if bad:
                raise RuntimeError(
                    f"SQLite foreign_key_check failed after {migrate_fn.__name__}: {bad}"
                )
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.commit()


async def run_sqlite_schema_migrations(async_engine: AsyncEngine = engine) -> None:
    """Apply SQLite-only schema fixes that create_all cannot express."""
    if "sqlite" not in str(async_engine.url):
        return

    # Table rebuilds must run with FK off outside a surrounding transaction.
    await _run_sqlite_table_rebuild(async_engine, migrate_sqlite_blocked_by_to_text)

    async with async_engine.begin() as conn:
        await conn.run_sync(migrate_sqlite_archive_fields)
        await conn.run_sync(migrate_sqlite_board_workflow_fields)
        await conn.run_sync(migrate_sqlite_ticket_workflow_fields)
        await conn.run_sync(migrate_sqlite_subscription_add_fields)

    await _run_sqlite_table_rebuild(async_engine, migrate_sqlite_subscription_filter_only)
    await _run_sqlite_table_rebuild(async_engine, migrate_sqlite_column_stages)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Dependency that provides an async SQLAlchemy session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
