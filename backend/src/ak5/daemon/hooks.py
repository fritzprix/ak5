"""Execute local subscriber hooks (events.md §4.1 / client.md §4.3).

Event data is passed only via env + stdin — never interpolated into the command.
Hooks run off the daemon event loop: per-subscriber sequential, across subscribers parallel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any

from ak5.daemon.paths import dead_letter_path
from ak5.daemon.state import DaemonState, LocalSubscriber

logger = logging.getLogger(__name__)

_subscriber_locks: dict[str, asyncio.Lock] = {}


def _hook_timeout() -> float:
    raw = os.environ.get("AK5_HOOK_TIMEOUT", "300").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 300.0


def _max_attempts() -> int:
    raw = os.environ.get("AK5_HOOK_MAX_ATTEMPTS", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _retry_base() -> float:
    raw = os.environ.get("AK5_HOOK_RETRY_BASE", "0.5").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.5


def _ticket_title(inner: dict[str, Any], data: dict[str, Any]) -> str:
    ticket = inner.get("ticket")
    if isinstance(ticket, dict) and ticket.get("title"):
        return str(ticket["title"])
    for key in ("title",):
        if inner.get(key):
            return str(inner[key])
        if data.get(key):
            return str(data[key])
    return ""


def hook_env_for_event(event: dict[str, Any]) -> dict[str, str]:
    """Build events.md §4.1 environ from a spool event row."""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if not data and isinstance(event.get("payload"), dict):
        data = event["payload"]
    # Nested server event shape: payload may be the full event
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(inner, dict):
        inner = {}
    title = _ticket_title(inner, data if isinstance(data, dict) else {})
    status = str(
        inner.get("to_stage")
        or inner.get("status")
        or inner.get("stage")
        or data.get("to_stage")
        or data.get("status")
        or data.get("stage")
        or ""
    )
    summary = str(
        inner.get("summary")
        or data.get("summary")
        or title
        or event.get("type")
        or ""
    )
    actor = str(
        inner.get("actor_id")
        or data.get("actor_id")
        or event.get("actor_id")
        or ""
    )
    # Prefer server event_id (evt_…); fall back to seq only if absent
    event_id = str(
        data.get("event_id")
        or data.get("id")
        or inner.get("event_id")
        or event.get("event_id")
        or (f"seq-{event['seq']}" if event.get("seq") is not None else "")
    )
    cursor = str(event.get("seq") or data.get("cursor") or "")
    env = os.environ.copy()
    env.update(
        {
            "AK5_EVENT": str(event.get("type") or ""),
            "AK5_EVENT_TYPE": str(event.get("type") or ""),
            "AK5_EVENT_ID": event_id,
            "AK5_CURSOR": cursor,
            "AK5_BOARD_ID": str(event.get("board_id") or data.get("board_id") or ""),
            "AK5_TICKET_ID": str(event.get("ticket_id") or data.get("ticket_id") or ""),
            "AK5_TITLE": title,
            "AK5_STATUS": status,
            "AK5_SUMMARY": summary,
            "AK5_ACTOR_ID": actor,
            "AK5_EVENT_ACTOR_ID": actor,
            "AK5_DATA_JSON": json.dumps(data if data else {}, ensure_ascii=False),
        }
    )
    return env


def append_dead_letter(
    *,
    subscriber_id: str,
    profile: str,
    event: dict[str, Any],
    reason: str,
) -> None:
    path = dead_letter_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    event_id = str(
        data.get("event_id")
        or event.get("event_id")
        or data.get("id")
        or (f"seq-{event.get('seq')}" if event.get("seq") is not None else "")
    )
    row = {
        "ts": time.time(),
        "subscriber_id": subscriber_id,
        "profile": profile,
        "event_id": event_id,
        "seq": event.get("seq"),
        "type": event.get("type"),
        "ticket_id": event.get("ticket_id"),
        "reason": reason,
        "event": event,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


async def run_subscriber_hook(sub: LocalSubscriber, event: dict[str, Any]) -> int:
    """Run hook in a worker thread so the daemon event loop stays responsive.

    ``asyncio.create_subprocess_shell`` + concurrent stdin pipes races under
    uvicorn/UDS (WriteUnixTransport closed); ``subprocess.run`` in
    ``asyncio.to_thread`` avoids that while keeping hooks off the loop.
    """
    env = hook_env_for_event(event)
    stdin = json.dumps(event, ensure_ascii=False).encode("utf-8")
    timeout = _hook_timeout()
    command = sub.exec_command

    def _run() -> int:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                input=stdin,
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            return int(proc.returncode)
        except subprocess.TimeoutExpired:
            return -9

    return await asyncio.to_thread(_run)


async def _deliver_one(state: DaemonState, sub: LocalSubscriber, event: dict[str, Any]) -> None:
    attempts = _max_attempts()
    base = _retry_base()
    last_reason = "unknown"
    for attempt in range(attempts):
        code = await run_subscriber_hook(sub, event)
        if code == 0:
            state.set_local_cursor(sub.id, int(event["seq"]))
            return
        last_reason = f"exit {code}" if code != -9 else "timeout"
        if attempt + 1 < attempts and base > 0:
            await asyncio.sleep(base * (2**attempt))
    append_dead_letter(
        subscriber_id=sub.id,
        profile=sub.profile,
        event=event,
        reason=last_reason,
    )
    state.mark_dead_letter(sub.id, f"{last_reason} on seq {event['seq']}")
    state.set_local_cursor(sub.id, int(event["seq"]))


async def _process_subscriber(state: DaemonState, sub: LocalSubscriber) -> None:
    lock = _subscriber_locks.setdefault(sub.id, asyncio.Lock())
    async with lock:
        batch = state.events_since(
            sub.profile,
            sub.local_cursor,
            limit=100,
            event_types=sub.events or None,
        )
        for event in batch:
            if sub.board_id and event.get("board_id") and event["board_id"] != sub.board_id:
                state.set_local_cursor(sub.id, int(event["seq"]))
                continue
            await _deliver_one(state, sub, event)


async def dispatch_new_events(state: DaemonState, profile: str) -> None:
    """Fan-out: subscribers in parallel; each subscriber processes events sequentially."""
    subs = state.list_subscribers(profile)
    if not subs:
        return
    results = await asyncio.gather(
        *[_process_subscriber(state, sub) for sub in subs],
        return_exceptions=True,
    )
    for sub, result in zip(subs, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("subscriber %s dispatch failed: %s", sub.id, result)
