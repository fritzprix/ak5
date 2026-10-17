"""Subscription filter matching, long-poll, ack, and event retention (events.md §2–§3).

Server does **not** execute shell hooks — that is the client daemon's job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ak5.config import settings
from ak5.contracts.errors import ErrorCode
from ak5.database import AsyncSessionLocal
from ak5.models.event import Event
from ak5.models.subscription import Subscription
from ak5.services.event_bus import event_bus
from ak5.services.event_context import (
    matches_agent_filter,
    matches_event_filter,
    normalize_actor_id,
)

logger = logging.getLogger("ak5.subscription_service")


class EventVisibilityPolicy(Protocol):
    """Backend-specific guard against seq/commit reordering (Postgres #20).

    Readers must not return a seq that may still have a lower uncommitted
    predecessor. SQLite writers are serialized, so the default is a no-op.
    """

    async def max_visible_seq(self, session: AsyncSession) -> int | None:
        """Inclusive ceiling for visible ``seq``, or ``None`` for no limit."""
        ...


class NoOpVisibilityPolicy:
    """SQLite / single-writer: all committed rows are immediately visible."""

    async def max_visible_seq(self, session: AsyncSession) -> int | None:
        return None


class CommitLagVisibilityPolicy:
    """Hold back events newer than ``window_seconds`` (Postgres-oriented)."""

    def __init__(self, window_seconds: float) -> None:
        self.window_seconds = window_seconds

    async def max_visible_seq(self, session: AsyncSession) -> int | None:
        if self.window_seconds <= 0:
            return None
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=self.window_seconds)
        result = await session.execute(
            select(func.max(Event.seq)).where(Event.created_at <= cutoff)
        )
        return result.scalar_one_or_none()


def build_visibility_policy(
    *,
    window_seconds: float | None = None,
) -> EventVisibilityPolicy:
    """Factory for the active visibility policy (injectable in tests)."""
    seconds = settings.EVENT_VISIBILITY_WINDOW_SECONDS if window_seconds is None else window_seconds
    if seconds and seconds > 0:
        return CommitLagVisibilityPolicy(seconds)
    return NoOpVisibilityPolicy()


class SubscriptionServiceError(Exception):
    """Domain error with a stable :class:`ErrorCode`."""

    def __init__(self, code: ErrorCode, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class SubscriptionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        visibility: EventVisibilityPolicy | None = None,
    ) -> None:
        self.session_factory = session_factory or AsyncSessionLocal
        self.visibility: EventVisibilityPolicy = visibility or build_visibility_policy()

    async def start(self) -> None:
        """No-op: server no longer runs a background exec listener (events.md §0)."""
        logger.info("SubscriptionService ready (filter/long-poll only; no server exec).")

    async def stop(self) -> None:
        """No-op counterpart to :meth:`start`."""
        return

    async def purge_expired_events(self, session: AsyncSession) -> int:
        """Lazy purge of events older than ``EVENT_RETENTION_DAYS``. Returns deleted count."""
        days = settings.EVENT_RETENTION_DAYS
        if days <= 0:
            return 0
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        result = await session.execute(delete(Event).where(Event.created_at < cutoff))
        return int(result.rowcount or 0)

    async def ensure_cursor_valid(self, session: AsyncSession, cursor: int) -> None:
        """Raise ``CURSOR_EXPIRED`` when ``cursor`` falls into a purged gap."""
        if cursor <= 0:
            return
        min_seq = (
            await session.execute(select(func.min(Event.seq)))
        ).scalar_one_or_none()
        if min_seq is None:
            return
        # Next expected seq after cursor is cursor+1; a gap means retention purged it.
        if min_seq > cursor + 1:
            raise SubscriptionServiceError(
                ErrorCode.CURSOR_EXPIRED,
                f"Cursor {cursor} is older than the event retention window "
                f"(oldest available seq={min_seq})",
                details={"cursor": cursor, "oldest_seq": min_seq},
            )

    def event_matches_filter(self, sub: Subscription, event: Event) -> bool:
        """Apply subscription filter to a persisted event row."""
        if sub.board_id and sub.board_id not in ("*", "all") and event.board_id != sub.board_id:
            return False

        if sub.events and not matches_event_filter(sub.events, event.type):
            return False

        data = event.data
        # role/capabilities only constrain WORK_AVAILABLE; other types pass
        # when the events filter matches.
        if (sub.role or sub.capabilities) and event.type == "WORK_AVAILABLE":
            if sub.role and str(data.get("required_role") or "") != sub.role:
                return False
            if sub.capabilities:
                try:
                    required = json.loads(sub.capabilities)
                except (json.JSONDecodeError, TypeError):
                    required = []
                if isinstance(required, list) and required:
                    event_caps = data.get("capabilities") or []
                    if not isinstance(event_caps, list):
                        event_caps = []
                    if not set(str(c) for c in required) & set(str(c) for c in event_caps):
                        return False

        if sub.for_agent:
            ticket_data = data.get("ticket") or data.get("subtask")
            if not isinstance(ticket_data, dict):
                ticket_data = {
                    "ticket_id": event.ticket_id,
                    "assigned_to": data.get("assigned_to"),
                    "board_id": event.board_id,
                }
            if not matches_agent_filter(sub.for_agent, ticket_data, data):
                return False

        return True

    def event_to_dict(self, event: Event) -> dict[str, Any]:
        created = event.created_at
        created_str = created.isoformat() if created is not None else None
        return {
            "seq": event.seq,
            "event_id": event.event_id,
            "type": event.type,
            "board_id": event.board_id,
            "ticket_id": event.ticket_id,
            "actor_id": event.actor_id,
            "data": event.data,
            "created_at": created_str,
        }

    async def fetch_events_since(
        self,
        session: AsyncSession,
        *,
        cursor: int,
        limit: int,
        board_id: str | None = None,
        sub: Subscription | None = None,
    ) -> list[Event]:
        """Load events with ``seq > cursor``, applying visibility + optional filter."""
        ceiling = await self.visibility.max_visible_seq(session)
        # Over-fetch when a subscription filter may reject rows.
        fetch_limit = max(limit * 4, limit) if sub is not None else limit

        stmt = select(Event).where(Event.seq > cursor).order_by(Event.seq.asc()).limit(fetch_limit)
        if ceiling is not None:
            stmt = stmt.where(Event.seq <= ceiling)
        filter_board = board_id if board_id is not None else (sub.board_id if sub else None)
        if filter_board and filter_board not in ("*", "all"):
            stmt = stmt.where(Event.board_id == filter_board)

        rows = list((await session.execute(stmt)).scalars().all())
        if sub is None:
            return rows[:limit]

        matched: list[Event] = []
        for ev in rows:
            if self.event_matches_filter(sub, ev):
                matched.append(ev)
                if len(matched) >= limit:
                    break
        return matched

    async def acquire_consumer_lease(
        self,
        session: AsyncSession,
        sub: Subscription,
        consumer_id: str,
    ) -> None:
        """Bind or renew consumer lease; raise ``CONSUMER_CONFLICT`` on clash."""
        clean = (consumer_id or "").strip()
        if not clean:
            raise SubscriptionServiceError(
                ErrorCode.PAYLOAD_INVALID,
                "X-AK5-Consumer header is required",
            )

        now = datetime.now(UTC).replace(tzinfo=None)
        if sub.consumer_id and sub.consumer_lease_expires_at:
            expires = sub.consumer_lease_expires_at
            if expires.tzinfo is not None:
                expires = expires.replace(tzinfo=None)
            if expires > now and normalize_actor_id(sub.consumer_id) != normalize_actor_id(clean):
                raise SubscriptionServiceError(
                    ErrorCode.CONSUMER_CONFLICT,
                    f"Subscription is leased by consumer '{sub.consumer_id}'",
                    details={
                        "consumer_id": sub.consumer_id,
                        "lease_expires_at": sub.consumer_lease_expires_at.isoformat(),
                    },
                )

        sub.consumer_id = clean
        sub.consumer_lease_expires_at = now + timedelta(seconds=settings.CONSUMER_LEASE_SECONDS)
        await session.commit()
        await session.refresh(sub)

    async def ack_cursor(
        self,
        session: AsyncSession,
        sub: Subscription,
        cursor: int,
        *,
        consumer_id: str | None = None,
    ) -> int:
        """Monotonic ack: only advance ``ack_cursor`` (events.md §3.2)."""
        if consumer_id is not None:
            await self.acquire_consumer_lease(session, sub, consumer_id)
        if cursor > sub.ack_cursor:
            sub.ack_cursor = cursor
            await session.commit()
            await session.refresh(sub)
        return sub.ack_cursor

    async def long_poll_events(
        self,
        *,
        sub: Subscription,
        cursor: int | None,
        wait: int,
        limit: int,
        consumer_id: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """Long-poll for subscription events (events.md §3).

        Returns ``(events, next_cursor)``.
        """
        wait = max(0, min(int(wait), settings.LONG_POLL_MAX_WAIT))
        limit = max(1, min(int(limit), 100))

        async with self.session_factory() as session:
            db_sub = await session.get(Subscription, sub.subscription_id)
            if db_sub is None:
                raise SubscriptionServiceError(
                    ErrorCode.SUBSCRIPTION_NOT_FOUND,
                    f"Subscription '{sub.subscription_id}' not found",
                )
            await self.purge_expired_events(session)
            await session.commit()

            await self.acquire_consumer_lease(session, db_sub, consumer_id)

            effective_cursor = db_sub.ack_cursor if cursor is None else cursor
            await self.ensure_cursor_valid(session, effective_cursor)

            matched = await self.fetch_events_since(
                session, cursor=effective_cursor, limit=limit, sub=db_sub
            )
            if matched:
                next_cursor = matched[-1].seq
                return [self.event_to_dict(e) for e in matched], next_cursor

        if wait <= 0:
            return [], effective_cursor

        deadline = time.monotonic() + wait
        use_notify = settings.LONG_POLL_USE_INPROCESS_NOTIFY
        poll_interval = max(0.05, settings.LONG_POLL_DB_POLL_INTERVAL)

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if use_notify:
                await event_bus.wait_for_notify(min(remaining, poll_interval))
            else:
                await asyncio.sleep(min(remaining, poll_interval))

            async with self.session_factory() as session:
                db_sub = await session.get(Subscription, sub.subscription_id)
                if db_sub is None:
                    raise SubscriptionServiceError(
                        ErrorCode.SUBSCRIPTION_NOT_FOUND,
                        f"Subscription '{sub.subscription_id}' not found",
                    )
                # Renew lease while waiting
                await self.acquire_consumer_lease(session, db_sub, consumer_id)
                matched = await self.fetch_events_since(
                    session, cursor=effective_cursor, limit=limit, sub=db_sub
                )
                if matched:
                    next_cursor = matched[-1].seq
                    return [self.event_to_dict(e) for e in matched], next_cursor

        return [], effective_cursor

    async def poll_events(
        self,
        session: AsyncSession,
        *,
        board_id: str | None,
        since: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """One-shot event log poll without a subscription (``GET /events``)."""
        limit = max(1, min(int(limit), 100))
        since = max(0, int(since))
        await self.purge_expired_events(session)
        await self.ensure_cursor_valid(session, since)
        rows = await self.fetch_events_since(
            session, cursor=since, limit=limit, board_id=board_id
        )
        if not rows:
            return [], since
        return [self.event_to_dict(e) for e in rows], rows[-1].seq


# Global singleton instance
subscription_service = SubscriptionService()
