"""SQLite state: local subscribers + event spool (client.md §4)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ak5.daemon.paths import state_db_path


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db = path or state_db_path()
    db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS subscribers (
            id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            board_id TEXT,
            events TEXT,
            exec_command TEXT NOT NULL,
            local_cursor INTEGER NOT NULL DEFAULT 0,
            dead_letter TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS spool (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            profile TEXT NOT NULL,
            event_type TEXT NOT NULL,
            ticket_id TEXT,
            board_id TEXT,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_spool_profile_seq ON spool(profile, seq);
        CREATE TABLE IF NOT EXISTS profile_server_cursor (
            profile TEXT PRIMARY KEY,
            subscription_id TEXT,
            ack_cursor INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()


@dataclass
class LocalSubscriber:
    id: str
    profile: str
    board_id: str | None
    events: list[str]
    exec_command: str
    local_cursor: int
    dead_letter: str | None
    created_at: float


class DaemonState:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._conn = _connect(path)
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add_subscriber(
        self,
        *,
        profile: str,
        exec_command: str,
        board_id: str | None = None,
        events: list[str] | None = None,
        subscriber_id: str | None = None,
    ) -> LocalSubscriber:
        sid = subscriber_id or f"sub-{uuid.uuid4().hex[:10]}"
        now = time.time()
        events_list = list(events or [])
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO subscribers (
                    id, profile, board_id, events, exec_command, local_cursor, dead_letter, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (sid, profile, board_id, json.dumps(events_list), exec_command, now),
            )
            self._conn.commit()
        return LocalSubscriber(
            id=sid,
            profile=profile,
            board_id=board_id,
            events=events_list,
            exec_command=exec_command,
            local_cursor=0,
            dead_letter=None,
            created_at=now,
        )

    def list_subscribers(self, profile: str | None = None) -> list[LocalSubscriber]:
        with self._lock:
            if profile:
                rows = self._conn.execute(
                    "SELECT * FROM subscribers WHERE profile = ? ORDER BY created_at",
                    (profile,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM subscribers ORDER BY created_at"
                ).fetchall()
            return [self._row_to_sub(r) for r in rows]

    def get_subscriber(self, subscriber_id: str) -> LocalSubscriber | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM subscribers WHERE id = ?", (subscriber_id,)
            ).fetchone()
            return self._row_to_sub(row) if row else None

    def remove_subscriber(self, subscriber_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM subscribers WHERE id = ?", (subscriber_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def set_local_cursor(self, subscriber_id: str, cursor: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET local_cursor = ? WHERE id = ?",
                (cursor, subscriber_id),
            )
            self._conn.commit()

    def mark_dead_letter(self, subscriber_id: str, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET dead_letter = ? WHERE id = ?",
                (reason, subscriber_id),
            )
            self._conn.commit()

    def append_spool(
        self,
        *,
        profile: str,
        event_type: str,
        payload: dict[str, Any],
        ticket_id: str | None = None,
        board_id: str | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO spool (profile, event_type, ticket_id, board_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    profile,
                    event_type,
                    ticket_id,
                    board_id,
                    json.dumps(payload),
                    time.time(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def events_since(
        self,
        profile: str,
        cursor: int,
        *,
        limit: int = 50,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT seq, event_type, ticket_id, board_id, payload, created_at
                FROM spool WHERE profile = ? AND seq > ? ORDER BY seq ASC LIMIT ?
                """,
                (profile, cursor, limit),
            ).fetchall()
            out: list[dict[str, Any]] = []
            allow = {e.upper() for e in event_types} if event_types else None
            for r in rows:
                et = str(r["event_type"])
                if allow is not None and et.upper() not in allow:
                    continue
                out.append(
                    {
                        "seq": int(r["seq"]),
                        "type": et,
                        "ticket_id": r["ticket_id"],
                        "board_id": r["board_id"],
                        "data": json.loads(r["payload"]),
                        "created_at": r["created_at"],
                    }
                )
            return out

    def get_server_cursor(self, profile: str) -> tuple[str | None, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT subscription_id, ack_cursor FROM profile_server_cursor WHERE profile = ?",
                (profile,),
            ).fetchone()
            if not row:
                return None, 0
            return row["subscription_id"], int(row["ack_cursor"])

    def set_server_cursor(
        self, profile: str, *, subscription_id: str | None, ack_cursor: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO profile_server_cursor (profile, subscription_id, ack_cursor)
                VALUES (?, ?, ?)
                ON CONFLICT(profile) DO UPDATE SET
                    subscription_id = excluded.subscription_id,
                    ack_cursor = excluded.ack_cursor
                """,
                (profile, subscription_id, ack_cursor),
            )
            self._conn.commit()

    @staticmethod
    def _row_to_sub(row: sqlite3.Row) -> LocalSubscriber:
        events_raw = row["events"] or "[]"
        try:
            events = json.loads(events_raw)
        except json.JSONDecodeError:
            events = []
        return LocalSubscriber(
            id=row["id"],
            profile=row["profile"],
            board_id=row["board_id"],
            events=list(events) if isinstance(events, list) else [],
            exec_command=row["exec_command"],
            local_cursor=int(row["local_cursor"]),
            dead_letter=row["dead_letter"],
            created_at=float(row["created_at"]),
        )
