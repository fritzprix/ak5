"""Execute local subscriber hooks (events.md §4.1 / client.md §4.3).

Event data is passed only via env + stdin — never interpolated into the command.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from ak5.daemon.state import DaemonState, LocalSubscriber


def run_subscriber_hook(sub: LocalSubscriber, event: dict[str, Any]) -> int:
    env = os.environ.copy()
    env.update(
        {
            "AK5_EVENT": str(event.get("type") or ""),
            "AK5_EVENT_TYPE": str(event.get("type") or ""),
            "AK5_BOARD_ID": str(event.get("board_id") or ""),
            "AK5_TICKET_ID": str(event.get("ticket_id") or ""),
            "AK5_SUMMARY": str(
                (event.get("data") or {}).get("summary")
                or event.get("type")
                or ""
            ),
            "AK5_DATA_JSON": json.dumps(event.get("data") or {}),
        }
    )
    stdin = json.dumps(event, ensure_ascii=False).encode("utf-8")
    proc = subprocess.run(
        sub.exec_command,
        shell=True,
        input=stdin,
        env=env,
        capture_output=True,
        check=False,
    )
    return int(proc.returncode)


def dispatch_new_events(state: DaemonState, profile: str) -> None:
    """Advance each subscriber's local cursor; failures go to dead-letter only."""
    for sub in state.list_subscribers(profile):
        batch = state.events_since(
            profile,
            sub.local_cursor,
            limit=100,
            event_types=sub.events or None,
        )
        for event in batch:
            if sub.board_id and event.get("board_id") and event["board_id"] != sub.board_id:
                state.set_local_cursor(sub.id, int(event["seq"]))
                continue
            code = run_subscriber_hook(sub, event)
            if code != 0:
                state._conn.execute(
                    "UPDATE subscribers SET dead_letter = ? WHERE id = ?",
                    (f"exit {code} on seq {event['seq']}", sub.id),
                )
                state._conn.commit()
            state.set_local_cursor(sub.id, int(event["seq"]))
