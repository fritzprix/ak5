"""In-memory wake-up signal for local SSE / long-poll waiters.

The persistent event log (``events`` table) is the source of truth and the only
place events are read from. This bus carries no events: after a successful
commit ``event_log.record_event`` calls :meth:`EventBus.publish`, which wakes
every waiter so it re-reads the log (#35 removed the unused history/subscriber
queue).
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._notify_waiters: set[asyncio.Event] = set()
        self._lock = asyncio.Lock()

    def _wake_notify_waiters(self) -> None:
        for waiter in list(self._notify_waiters):
            waiter.set()

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Wake waiters after an event was committed (payload stays in the log)."""
        _ = (event_type, data)
        async with self._lock:
            self._wake_notify_waiters()

    async def notify(self) -> None:
        """Wake long-poll / SSE waiters (test helper)."""
        async with self._lock:
            self._wake_notify_waiters()

    async def wait_for_notify(self, timeout: float) -> bool:
        """Wait until a publish/notify or ``timeout`` seconds. Returns True if woken."""
        if timeout <= 0:
            return False
        waiter = asyncio.Event()
        async with self._lock:
            self._notify_waiters.add(waiter)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False
        finally:
            async with self._lock:
                self._notify_waiters.discard(waiter)


# Singleton event bus instance
event_bus = EventBus()
