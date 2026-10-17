#!/usr/bin/env python3
"""Build a realistic v1 SQLite DB for migration regression tests (#29).

Does not commit a binary fixture into the repo — callers pass an output path.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

V1_DDL = [
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
    """,
    """
    CREATE TABLE boards (
        board_id VARCHAR(128) PRIMARY KEY,
        name VARCHAR(256) NOT NULL,
        description TEXT,
        created_by VARCHAR(128) NOT NULL,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(created_by) REFERENCES actors (actor_id)
    )
    """,
    """
    CREATE TABLE columns (
        column_id VARCHAR(128) PRIMARY KEY,
        board_id VARCHAR(128) NOT NULL,
        name VARCHAR(128) NOT NULL,
        stage VARCHAR(32) NOT NULL,
        position INTEGER NOT NULL,
        wip_limit INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(board_id) REFERENCES boards (board_id) ON DELETE CASCADE,
        CONSTRAINT check_column_stage CHECK (
            stage IN ('open', 'in_progress', 'review', 'done')
        )
    )
    """,
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
        blocked_by TEXT,
        execution_context TEXT,
        due_date DATETIME,
        is_archived BOOLEAN NOT NULL DEFAULT 0,
        archived_at DATETIME,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(board_id) REFERENCES boards (board_id) ON DELETE CASCADE,
        FOREIGN KEY(column_id) REFERENCES columns (column_id),
        FOREIGN KEY(parent_ticket_id) REFERENCES tickets (ticket_id) ON DELETE SET NULL,
        FOREIGN KEY(assigned_to) REFERENCES actors (actor_id) ON DELETE SET NULL,
        FOREIGN KEY(created_by) REFERENCES actors (actor_id),
        CONSTRAINT check_ticket_priority
            CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
        CONSTRAINT check_ticket_status
            CHECK (status IN ('open', 'in_progress', 'blocked', 'done'))
    )
    """,
    """
    CREATE TABLE ticket_comments (
        comment_id VARCHAR(64) PRIMARY KEY,
        ticket_id VARCHAR(64) NOT NULL,
        actor_id VARCHAR(128) NOT NULL,
        content TEXT NOT NULL,
        is_internal BOOLEAN NOT NULL DEFAULT 0,
        metadata_json TEXT DEFAULT '{}',
        created_at DATETIME NOT NULL,
        FOREIGN KEY(ticket_id) REFERENCES tickets (ticket_id) ON DELETE CASCADE,
        FOREIGN KEY(actor_id) REFERENCES actors (actor_id)
    )
    """,
    """
    CREATE TABLE ticket_attachments (
        attachment_id VARCHAR(64) PRIMARY KEY,
        ticket_id VARCHAR(64) NOT NULL,
        actor_id VARCHAR(128) NOT NULL,
        filename VARCHAR(512) NOT NULL,
        file_size INTEGER NOT NULL,
        content_type VARCHAR(128) NOT NULL,
        storage_path VARCHAR(1024) NOT NULL,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(ticket_id) REFERENCES tickets (ticket_id) ON DELETE CASCADE,
        FOREIGN KEY(actor_id) REFERENCES actors (actor_id)
    )
    """,
    """
    CREATE TABLE subscriptions (
        subscription_id VARCHAR(128) PRIMARY KEY,
        board_id VARCHAR(128),
        exec_command TEXT NOT NULL,
        events TEXT,
        for_agent VARCHAR(128),
        ignore_actor VARCHAR(128),
        debounce_seconds FLOAT NOT NULL DEFAULT 0.0,
        created_by VARCHAR(128) NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
]


def build_v1_fixture(db_path: Path) -> int:
    """Create v1 schema + representative rows. Returns ticket count."""
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        for ddl in V1_DDL:
            conn.execute(ddl)

        conn.execute(
            """
            INSERT INTO actors (
                actor_id, actor_type, name, role, capabilities, status, created_at, updated_at
            ) VALUES
                ('user_pm', 'human', 'PM', 'PM', '[]', 'idle', '2026-01-01', '2026-01-01'),
                ('agent_legacy', 'agent', 'Legacy', 'worker', '[]', 'idle', '2026-01-01', '2026-01-01')
            """
        )
        conn.executemany(
            "INSERT INTO boards (board_id, name, created_by, created_at) VALUES (?, ?, 'user_pm', '2026-01-01')",
            [("b1", "Board One"), ("b2", "Board Two")],
        )
        for board_id, prefix in (("b1", "c1"), ("b2", "c2")):
            conn.execute(
                f"""
                INSERT INTO columns (
                    column_id, board_id, name, stage, position, wip_limit, created_at
                ) VALUES
                    ('{prefix}_open', '{board_id}', 'To Do', 'open', 1, 0, '2026-01-01'),
                    ('{prefix}_ip', '{board_id}', 'In Progress', 'in_progress', 2, 3, '2026-01-01'),
                    ('{prefix}_rev', '{board_id}', 'Review', 'review', 3, 0, '2026-01-01'),
                    ('{prefix}_done', '{board_id}', 'Done', 'done', 4, 0, '2026-01-01')
                """
            )

        tickets = [
            ("t_open", "b1", "c1_open", "Open task", "open", "open"),
            ("t_ip", "b1", "c1_ip", "In progress task", "in_progress", "in_progress"),
            ("t_rev", "b1", "c1_rev", "Review task", "in_progress", "review"),
            ("t_done", "b1", "c1_done", "Done task", "done", "done"),
            ("t_b2", "b2", "c2_open", "Other board", "open", "open"),
        ]
        for i, (tid, bid, cid, title, status, _stage) in enumerate(tickets):
            conn.execute(
                """
                INSERT INTO tickets (
                    ticket_id, board_id, column_id, parent_ticket_id, title, description,
                    priority, rank, labels, assigned_to, created_by, status, blocked_by,
                    execution_context, due_date, is_archived, archived_at, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, NULL, 'medium', ?, '[]', NULL, 'user_pm', ?, NULL,
                          NULL, NULL, 0, NULL, '2026-01-01', '2026-01-01')
                """,
                (tid, bid, cid, title, chr(ord("a") + i), status),
            )

        conn.execute(
            """
            INSERT INTO ticket_comments (
                comment_id, ticket_id, actor_id, content, is_internal, metadata_json, created_at
            ) VALUES ('cm1', 't_open', 'user_pm', 'legacy note', 0, '{}', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO ticket_attachments (
                attachment_id, ticket_id, actor_id, filename, file_size, content_type,
                storage_path, created_at
            ) VALUES ('att1', 't_open', 'user_pm', 'note.txt', 4, 'text/plain',
                      '/tmp/note.txt', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO subscriptions (
                subscription_id, board_id, exec_command, created_by, created_at
            ) VALUES ('sub1', 'b1', 'echo hi', 'user_pm', '2026-01-01')
            """
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        return int(n)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        type=Path,
        help="Path to write the v1 SQLite database (e.g. /tmp/ak5_v1_fixture.db)",
    )
    args = parser.parse_args()
    count = build_v1_fixture(args.output)
    print(f"wrote {args.output} with {count} tickets")


if __name__ == "__main__":
    main()
