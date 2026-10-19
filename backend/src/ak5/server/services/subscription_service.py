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

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ak5.contracts.errors import ErrorCode
from ak5.server.config import settings
from ak5.server.database import AsyncSessionLocal
from ak5.server.models.event import Event
from ak5.server.models.subscription import Subscription
from ak5.server.services.event_bus import event_bus
from ak5.server.services.event_context import (
    matches_agent_filter,
    matches_event_filter,
    normalize_actor_id,
)

logger = logging.getLogger("ak5.subscription_service")

# Process-local throttle for lazy event purge (avoids SQLite write lock storms).
_last_purge_mono: float = 0.0

_META_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS event_log_meta (
    key VARCHAR(64) NOT NULL PRIMARY KEY,
    value INTEGER NOT NULL
)
"""
_PURGED_THROUGH_KEY = "purged_through_seq"


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


def reset_purge_throttle_for_tests() -> None:
    """Allow the next purge to run immediately (test helper)."""
    global _last_purge_mono
    _last_purge_mono = 0.0


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

    async def max_event_seq(self, session: AsyncSession) -> int:
        """Current ``max(events.seq)``, or ``0`` when the log is empty."""
        result = await session.execute(select(func.max(Event.seq)))
        return int(result.scalar_one_or_none() or 0)

    async def compute_event_log_lag_seconds(self, session: AsyncSession) -> float:
        """Lag in seconds for the oldest unacknowledged subscription (operations.md §4.3, #137).

        Returns 0.0 if there are no subscriptions or all subscriptions are caught up.
        Indexed lookup: O(1) on events.seq PK without scanning the event log table.
        """
        min_ack_result = await session.execute(select(func.min(Subscription.ack_cursor)))
        min_ack = min_ack_result.scalar_one_or_none()
        if min_ack is None:
            return 0.0

        oldest_created_result = await session.execute(
            select(Event.created_at)
            .where(Event.seq > min_ack)
            .order_by(Event.seq.asc())
            .limit(1)
        )
        oldest_created = oldest_created_result.scalar_one_or_none()
        if oldest_created is None:
            return 0.0

        now = datetime.now(UTC)
        if oldest_created.tzinfo is None:
            oldest_dt = oldest_created.replace(tzinfo=UTC)
        else:
            oldest_dt = oldest_created.astimezone(UTC)

        return max(0.0, (now - oldest_dt).total_seconds())

    async def _ensure_meta_table(self, session: AsyncSession) -> None:
        await session.execute(text(_META_TABLE_DDL))

    async def get_purged_through_seq(self, session: AsyncSession) -> int:
        """Highest seq known to have been removed by retention purge."""
        await self._ensure_meta_table(session)
        result = await session.execute(
            text("SELECT value FROM event_log_meta WHERE key = :k"),
            {"k": _PURGED_THROUGH_KEY},
        )
        raw = result.scalar_one_or_none()
        return int(raw or 0)

    async def _set_purged_through_seq(self, session: AsyncSession, through: int) -> None:
        if through <= 0:
            return
        await self._ensure_meta_table(session)
        current = await self.get_purged_through_seq(session)
        if through <= current:
            return
        # Upsert without dialect-specific ON CONFLICT for broader backend support.
        existing = (
            await session.execute(
                text("SELECT 1 FROM event_log_meta WHERE key = :k"),
                {"k": _PURGED_THROUGH_KEY},
            )
        ).scalar_one_or_none()
        if existing:
            await session.execute(
                text("UPDATE event_log_meta SET value = :v WHERE key = :k"),
                {"k": _PURGED_THROUGH_KEY, "v": through},
            )
        else:
            await session.execute(
                text("INSERT INTO event_log_meta (key, value) VALUES (:k, :v)"),
                {"k": _PURGED_THROUGH_KEY, "v": through},
            )

    async def purge_expired_events(self, session: AsyncSession) -> int:
        """Lazy purge of events older than ``EVENT_RETENTION_DAYS``. Returns deleted count.

        Throttled per-process to avoid SQLite write-lock contention under many daemons.
        """
        global _last_purge_mono
        days = settings.EVENT_RETENTION_DAYS
        if days <= 0:
            return 0

        interval = max(0.0, float(settings.EVENT_PURGE_MIN_INTERVAL_SECONDS))
        now_mono = time.monotonic()
        if interval > 0 and (now_mono - _last_purge_mono) < interval:
            return 0

        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        max_purged = (
            await session.execute(
                select(func.max(Event.seq)).where(Event.created_at < cutoff)
            )
        ).scalar_one_or_none()

        result = await session.execute(delete(Event).where(Event.created_at < cutoff))
        deleted = int(result.rowcount or 0)
        if deleted and max_purged is not None:
            await self._set_purged_through_seq(session, int(max_purged))

        _last_purge_mono = now_mono
        return deleted

    async def ensure_cursor_valid(self, session: AsyncSession, cursor: int) -> None:
        """Raise ``CURSOR_EXPIRED`` when ``cursor`` falls into a purged range.

        Uses persisted ``purged_through_seq`` so Postgres sequence holes are not
        mistaken for retention gaps.
        """
        if cursor <= 0:
            return
        purged_through = await self.get_purged_through_seq(session)
        if cursor < purged_through:
            raise SubscriptionServiceError(
                ErrorCode.CURSOR_EXPIRED,
                f"Cursor {cursor} is older than the event retention window "
                f"(purged through seq={purged_through})",
                details={"cursor": cursor, "purged_through_seq": purged_through},
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
        workspace_id: str | None = None,
    ) -> tuple[list[Event], int]:
        """Load events with ``seq > cursor``, applying visibility + optional filter.

        Returns ``(matched_events, next_cursor)``.

        When a subscription filter rejects rows, ``next_cursor`` still advances to
        the last *scanned* seq so clients cannot starve behind a wall of
        non-matching events. Cursor stops at the last matched seq only when
        ``limit`` matches were collected.
        """
        ceiling = await self.visibility.max_visible_seq(session)
        # Over-fetch when a subscription filter may reject rows.
        fetch_limit = max(limit * 4, limit) if sub is not None else limit

        stmt = select(Event).where(Event.seq > cursor).order_by(Event.seq.asc()).limit(fetch_limit)
        if ceiling is not None:
            stmt = stmt.where(Event.seq <= ceiling)
        ws = workspace_id if workspace_id is not None else (sub.workspace_id if sub else None)
        if ws:
            stmt = stmt.where(Event.workspace_id == ws)
        filter_board = board_id if board_id is not None else (sub.board_id if sub else None)
        if filter_board and filter_board not in ("*", "all"):
            stmt = stmt.where(Event.board_id == filter_board)

        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return [], cursor

        if sub is None:
            return rows[:limit], rows[min(limit, len(rows)) - 1].seq

        matched: list[Event] = []
        last_scanned_seq = cursor
        for ev in rows:
            last_scanned_seq = ev.seq
            if self.event_matches_filter(sub, ev):
                matched.append(ev)
                if len(matched) >= limit:
                    return matched, ev.seq

        # Partial or empty match: advance past everything we scanned.
        return matched, last_scanned_seq

    async def acquire_consumer_lease(
        self,
        session: AsyncSession,
        sub: Subscription,
        consumer_id: str,
    ) -> None:
        """Bind or renew consumer lease atomically; raise ``CONSUMER_CONFLICT`` on clash."""
        clean = normalize_actor_id((consumer_id or "").strip()) or (consumer_id or "").strip()
        if not clean:
            raise SubscriptionServiceError(
                ErrorCode.PAYLOAD_INVALID,
                "X-AK5-Consumer header is required",
            )

        now = datetime.now(UTC).replace(tzinfo=None)
        expires = now + timedelta(seconds=settings.CONSUMER_LEASE_SECONDS)

        result = await session.execute(
            update(Subscription)
            .where(
                Subscription.subscription_id == sub.subscription_id,
                or_(
                    Subscription.consumer_id.is_(None),
                    Subscription.consumer_id == clean,
                    Subscription.consumer_lease_expires_at.is_(None),
                    Subscription.consumer_lease_expires_at <= now,
                ),
            )
            .values(consumer_id=clean, consumer_lease_expires_at=expires)
        )
        if int(result.rowcount or 0) == 0:
            await session.refresh(sub)
            holder = sub.consumer_id or "unknown"
            lease_iso = (
                sub.consumer_lease_expires_at.isoformat()
                if sub.consumer_lease_expires_at is not None
                else None
            )
            raise SubscriptionServiceError(
                ErrorCode.CONSUMER_CONFLICT,
                f"Subscription is leased by consumer '{holder}'",
                details={
                    "consumer_id": holder,
                    "lease_expires_at": lease_iso,
                },
            )

        await session.commit()
        await session.refresh(sub)

    def _lease_is_active(self, sub: Subscription, *, now: datetime | None = None) -> bool:
        if not sub.consumer_id or sub.consumer_lease_expires_at is None:
            return False
        now = now or datetime.now(UTC).replace(tzinfo=None)
        expires = sub.consumer_lease_expires_at
        if expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        return expires > now

    async def ack_cursor(
        self,
        session: AsyncSession,
        sub: Subscription,
        cursor: int,
        *,
        consumer_id: str | None = None,
    ) -> int:
        """Monotonic ack via conditional UPDATE (events.md §3.2).

        While a consumer lease is active, ``consumer_id`` is required and must
        match the lease holder.
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        if self._lease_is_active(sub, now=now):
            clean = (consumer_id or "").strip()
            if not clean:
                raise SubscriptionServiceError(
                    ErrorCode.CONSUMER_CONFLICT,
                    "X-AK5-Consumer header is required while a consumer lease is active",
                    details={"consumer_id": sub.consumer_id},
                )
            if normalize_actor_id(clean) != normalize_actor_id(sub.consumer_id or ""):
                raise SubscriptionServiceError(
                    ErrorCode.CONSUMER_CONFLICT,
                    f"Subscription is leased by consumer '{sub.consumer_id}'",
                    details={
                        "consumer_id": sub.consumer_id,
                        "lease_expires_at": (
                            sub.consumer_lease_expires_at.isoformat()
                            if sub.consumer_lease_expires_at
                            else None
                        ),
                    },
                )
            await self.acquire_consumer_lease(session, sub, clean)
        elif consumer_id is not None and consumer_id.strip():
            await self.acquire_consumer_lease(session, sub, consumer_id)

        result = await session.execute(
            update(Subscription)
            .where(
                Subscription.subscription_id == sub.subscription_id,
                Subscription.ack_cursor < cursor,
            )
            .values(ack_cursor=cursor)
        )
        if int(result.rowcount or 0) > 0:
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

        Returns ``(events, next_cursor)``. ``next_cursor`` advances through
        scanned non-matching events so filter walls cannot starve the client.
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

            # Drain filter-mismatch batches immediately so a wall of non-matching
            # events cannot pin next_cursor forever within a single request.
            while True:
                matched, next_cursor = await self.fetch_events_since(
                    session, cursor=effective_cursor, limit=limit, sub=db_sub
                )
                if matched:
                    return [self.event_to_dict(e) for e in matched], next_cursor
                if next_cursor <= effective_cursor:
                    break
                effective_cursor = next_cursor

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
                while True:
                    matched, next_cursor = await self.fetch_events_since(
                        session, cursor=effective_cursor, limit=limit, sub=db_sub
                    )
                    if matched:
                        return [self.event_to_dict(e) for e in matched], next_cursor
                    if next_cursor <= effective_cursor:
                        break
                    effective_cursor = next_cursor

        return [], effective_cursor

    async def poll_events(
        self,
        session: AsyncSession,
        *,
        board_id: str | None,
        since: int,
        limit: int,
        workspace_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """One-shot event log poll without a subscription (``GET /events``)."""
        limit = max(1, min(int(limit), 100))
        since = max(0, int(since))
        await self.purge_expired_events(session)
        await self.ensure_cursor_valid(session, since)
        rows, next_cursor = await self.fetch_events_since(
            session,
            cursor=since,
            limit=limit,
            board_id=board_id,
            workspace_id=workspace_id,
        )
        if not rows:
            return [], next_cursor
        return [self.event_to_dict(e) for e in rows], next_cursor


# Global singleton instance
subscription_service = SubscriptionService()
