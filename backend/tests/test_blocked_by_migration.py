from ak5.database import migrate_sqlite_blocked_by_to_text
from sqlalchemy import create_engine, text


def test_migrate_sqlite_blocked_by_drops_ticket_fk():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                """
                CREATE TABLE actors (
                    actor_id VARCHAR(128) PRIMARY KEY,
                    actor_type VARCHAR(16) NOT NULL,
                    name VARCHAR(256) NOT NULL,
                    role VARCHAR(128) NOT NULL,
                    description TEXT,
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    status VARCHAR(16) NOT NULL DEFAULT 'idle',
                    avatar_url VARCHAR(512),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE boards (
                    board_id VARCHAR(128) PRIMARY KEY,
                    name VARCHAR(256) NOT NULL,
                    description TEXT,
                    created_by VARCHAR(128) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(created_by) REFERENCES actors (actor_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE columns (
                    column_id VARCHAR(128) PRIMARY KEY,
                    board_id VARCHAR(128) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    stage VARCHAR(32) NOT NULL,
                    position INTEGER NOT NULL,
                    wip_limit INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(board_id) REFERENCES boards (board_id)
                )
                """
            )
        )
        # Legacy schema: blocked_by FK → tickets.ticket_id
        conn.execute(
            text(
                """
                CREATE TABLE tickets (
                    ticket_id VARCHAR(64) PRIMARY KEY,
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
                    blocked_by VARCHAR(64),
                    execution_context TEXT,
                    due_date DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(board_id) REFERENCES boards (board_id) ON DELETE CASCADE,
                    FOREIGN KEY(column_id) REFERENCES columns (column_id),
                    FOREIGN KEY(parent_ticket_id) REFERENCES tickets (ticket_id) ON DELETE SET NULL,
                    FOREIGN KEY(assigned_to) REFERENCES actors (actor_id) ON DELETE SET NULL,
                    FOREIGN KEY(created_by) REFERENCES actors (actor_id),
                    FOREIGN KEY(blocked_by) REFERENCES tickets (ticket_id) ON DELETE SET NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO actors (
                    actor_id, actor_type, name, role, capabilities, status, created_at, updated_at
                ) VALUES (
                    'user_pm', 'human', 'PM', 'PM', '[]', 'idle', '2026-01-01', '2026-01-01'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO boards (
                    board_id, name, created_by, created_at, updated_at
                ) VALUES (
                    'b1', 'Board', 'user_pm', '2026-01-01', '2026-01-01'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO columns (
                    column_id, board_id, name, stage, position, wip_limit, created_at, updated_at
                ) VALUES (
                    'c1', 'b1', 'To Do', 'open', 1, 0, '2026-01-01', '2026-01-01'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO tickets (
                    ticket_id, board_id, column_id, title, priority, rank, labels,
                    created_by, status, created_at, updated_at
                ) VALUES (
                    'TK-001', 'b1', 'c1', 'Task', 'medium', '0|a', '[]',
                    'user_pm', 'open', '2026-01-01', '2026-01-01'
                )
                """
            )
        )

        migrate_sqlite_blocked_by_to_text(conn)

        fk_rows = conn.execute(text("PRAGMA foreign_key_list(tickets)")).mappings().all()
        assert not any(row["from"] == "blocked_by" for row in fk_rows)

        # Free-text block reason must succeed under FK enforcement.
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                """
                UPDATE tickets
                SET status = 'blocked', blocked_by = 'waiting on design review'
                WHERE ticket_id = 'TK-001'
                """
            )
        )
        reason = conn.execute(
            text("SELECT blocked_by FROM tickets WHERE ticket_id = 'TK-001'")
        ).scalar()
        assert reason == "waiting on design review"
