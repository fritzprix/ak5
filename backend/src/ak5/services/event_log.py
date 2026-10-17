"""Persistent event log writer (events.md §1).

``record_event`` only ``session.add``s — the caller owns the commit.
After a successful commit, pending events are published to the in-memory
``event_bus`` for local immediate wake-up (SSE / long-poll notify).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ak5.contracts.events import EventType
from ak5.models.event import Event

logger = logging.getLogger(__name__)

_PENDING_KEY = "ak5_pending_bus_events"
_HOOK_KEY = "ak5_event_bus_hook"


def _bus_payload(ev: Event) -> dict[str, Any]:
    """Shape published to the in-memory bus (keeps legacy SSE fields useful)."""
    payload = dict(ev.data)
    payload.setdefault("board_id", ev.board_id)
    payload.setdefault("ticket_id", ev.ticket_id)
    payload.setdefault("actor_id", ev.actor_id)
    payload["seq"] = ev.seq
    payload["event_id"] = ev.event_id
    return payload


def _schedule_bus_publish(event_type: str, data: dict[str, Any]) -> None:
    try:
        from ak5.services.event_bus import event_bus
    except Exception:
        logger.exception("event_bus import failed; skipping notify")
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("No running loop for event_bus notify (type=%s)", event_type)
        return

    async def _publish() -> None:
        try:
            await event_bus.publish(event_type, data)
        except Exception:
            logger.exception("event_bus.publish failed for type=%s", event_type)

    loop.create_task(_publish())


def _on_after_commit(session: Session) -> None:
    pending: list[dict[str, Any]] = session.info.pop(_PENDING_KEY, [])
    if not pending:
        return
    for item in pending:
        _schedule_bus_publish(item["type"], item["data"])


def _on_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def _ensure_session_hooks(sync_session: Session) -> None:
    if sync_session.info.get(_HOOK_KEY):
        return
    sync_session.info[_HOOK_KEY] = True
    sa_event.listen(sync_session, "after_commit", _on_after_commit)
    sa_event.listen(sync_session, "after_rollback", _on_after_rollback)


async def record_event(
    session: AsyncSession,
    type: EventType | str,
    *,
    board_id: str | None,
    ticket_id: str | None,
    actor_id: str | None,
    data: dict[str, Any],
) -> Event:
    """Append an event to the caller's session (no commit).

    After the caller commits, the in-memory ``event_bus`` is notified.
    """
    event_type = type.value if isinstance(type, EventType) else str(type)
    event = Event(
        event_id=f"evt_{uuid.uuid4().hex}",
        type=event_type,
        board_id=board_id,
        ticket_id=ticket_id,
        actor_id=actor_id,
        data_json=json.dumps(data),
    )
    session.add(event)
    await session.flush()  # assign monotonic seq before commit

    sync = session.sync_session
    _ensure_session_hooks(sync)
    pending: list[dict[str, Any]] = sync.info.setdefault(_PENDING_KEY, [])
    pending.append({"type": event_type, "data": _bus_payload(event)})
    return event
