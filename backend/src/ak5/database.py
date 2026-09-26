from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ak5.config import settings

# Configure SQLite async engine with WAL mode and busy_timeout
connect_args = {}
if "sqlite" in settings.DATABASE_URL:
    connect_args["timeout"] = settings.SQLITE_BUSY_TIMEOUT / 1000.0

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
    """
    exists = sync_conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets'")
    ).scalar()
    if not exists:
        return
    if not _sqlite_blocked_by_has_ticket_fk(sync_conn):
        return

    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
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
    sync_conn.execute(
        text(
            """
            INSERT INTO tickets__mig_blocked_by (
                ticket_id, board_id, column_id, parent_ticket_id, title, description,
                priority, rank, labels, assigned_to, created_by, status, blocked_by,
                execution_context, due_date, created_at, updated_at
            )
            SELECT
                ticket_id, board_id, column_id, parent_ticket_id, title, description,
                priority, rank, labels, assigned_to, created_by, status, blocked_by,
                execution_context, due_date, created_at, updated_at
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
    sync_conn.execute(text("PRAGMA foreign_keys=ON"))


async def run_sqlite_schema_migrations(async_engine: AsyncEngine = engine) -> None:
    """Apply SQLite-only schema fixes that create_all cannot express."""
    if "sqlite" not in str(async_engine.url):
        return
    async with async_engine.begin() as conn:
        await conn.run_sync(migrate_sqlite_blocked_by_to_text)


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
