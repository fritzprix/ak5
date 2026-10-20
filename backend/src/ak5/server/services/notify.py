"""Cross-instance event wake-up (deployment.md §3, #108).

Modes:
- ``inproc``: in-memory :class:`~ak5.server.services.event_bus.EventBus` (single process).
- ``poll``: waiters sleep and re-read ``max(seq)`` / events (no cross-process signal).
- ``pg``: PostgreSQL ``LISTEN/NOTIFY`` plus local bus wake for waiters on this process.
- ``redis``: reserved; rejected at startup until implemented.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ak5.server.config import get_notify_mode, notify_poll_interval_seconds, settings
from ak5.server.services.event_bus import event_bus

logger = logging.getLogger(__name__)

PG_NOTIFY_CHANNEL = "ak5_events"

_listener_task: asyncio.Task[None] | None = None
_listener_stop: asyncio.Event | None = None


def _asyncpg_dsn(database_url: str) -> str:
    """Convert SQLAlchemy URL to an asyncpg DSN (postgresql://…)."""
    url = database_url.strip()
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
            break
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


async def publish_wake(*, event_type: str = "", data: dict[str, Any] | None = None) -> None:
    """Wake local waiters and, in ``pg`` mode, signal other instances."""
    _ = data
    mode = get_notify_mode()
    if mode in ("inproc", "pg"):
        await event_bus.publish(event_type or "wake", data or {})
    if mode == "pg":
        await _pg_notify()


async def wait_for_wake(timeout: float) -> bool:
    """Block until a wake signal or ``timeout`` seconds.

    Returns True when woken by inproc/pg notify; False on timeout or poll sleep.
    """
    if timeout <= 0:
        return False
    mode = get_notify_mode()
    if mode in ("inproc", "pg"):
        return await event_bus.wait_for_notify(timeout)
    await asyncio.sleep(min(timeout, notify_poll_interval_seconds()))
    return False


def wake_wait_slice(remaining: float) -> float:
    """How long one wait iteration should block (lease renew / poll cadence)."""
    mode = get_notify_mode()
    if mode == "poll":
        return min(remaining, notify_poll_interval_seconds())
    return min(remaining, max(0.05, float(settings.LONG_POLL_DB_POLL_INTERVAL)))


async def _pg_notify() -> None:
    try:
        import asyncpg
    except ImportError:
        logger.warning("asyncpg not installed; skipping pg_notify")
        return

    dsn = _asyncpg_dsn(settings.DATABASE_URL)
    try:
        conn = await asyncpg.connect(dsn)
    except Exception:
        logger.exception("pg_notify connect failed")
        return
    try:
        await conn.execute(f"SELECT pg_notify('{PG_NOTIFY_CHANNEL}', '')")
    except Exception:
        logger.exception("pg_notify failed")
    finally:
        await conn.close()


async def _pg_listen_loop(stop: asyncio.Event) -> None:
    try:
        import asyncpg
    except ImportError:
        logger.error("AK5_NOTIFY=pg requires asyncpg")
        return

    dsn = _asyncpg_dsn(settings.DATABASE_URL)
    while not stop.is_set():
        conn = None
        try:
            conn = await asyncpg.connect(dsn)

            def _on_notify(
                _connection: object,
                _pid: int,
                _channel: str,
                _payload: str,
            ) -> None:
                asyncio.get_running_loop().create_task(event_bus.notify())

            await conn.add_listener(PG_NOTIFY_CHANNEL, _on_notify)
            logger.info("PostgreSQL LISTEN on %s", PG_NOTIFY_CHANNEL)
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30.0)
                except TimeoutError:
                    await conn.execute("SELECT 1")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("pg LISTEN loop error; retrying in 2s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except TimeoutError:
                pass
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass


@asynccontextmanager
async def notify_lifespan() -> AsyncIterator[None]:
    """Start/stop the pg LISTEN task when ``AK5_NOTIFY=pg``."""
    global _listener_task, _listener_stop
    from ak5.server.config import validate_notify_config

    validate_notify_config()
    mode = get_notify_mode()
    if mode == "pg":
        _listener_stop = asyncio.Event()
        _listener_task = asyncio.create_task(
            _pg_listen_loop(_listener_stop), name="ak5-pg-notify-listener"
        )
    try:
        yield
    finally:
        if _listener_stop is not None:
            _listener_stop.set()
        if _listener_task is not None:
            _listener_task.cancel()
            try:
                await _listener_task
            except asyncio.CancelledError:
                pass
            _listener_task = None
            _listener_stop = None
