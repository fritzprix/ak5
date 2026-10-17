"""In-memory event bus for local immediate wake-up (SSE / long-poll notify).

The persistent event log (``events`` table) is the source of truth. This bus
only notifies waiters after a successful commit (see ``event_log.record_event``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class BoardEvent:
    event_id: int
    event_type: str
    data: dict[str, Any]
    timestamp: str

    def to_sse_format(self) -> str:
        return (
            f"id: {self.event_id}\n"
            f"event: {self.event_type}\n"
            f"data: {json.dumps(self.data)}\n\n"
        )


class EventBus:
    def __init__(self, max_history: int = 200) -> None:
        self._counter: int = 0
        self._history: deque[BoardEvent] = deque(maxlen=max_history)
        self._subscribers: set[asyncio.Queue[BoardEvent]] = set()
        self._notify_waiters: set[asyncio.Event] = set()
        self._lock = asyncio.Lock()

    def _wake_notify_waiters(self) -> None:
        for waiter in list(self._notify_waiters):
            waiter.set()

    async def publish(self, event_type: str, data: dict[str, Any]) -> BoardEvent:
        async with self._lock:
            self._counter += 1
            event = BoardEvent(
                event_id=self._counter,
                event_type=event_type,
                data=data,
                timestamp=datetime.now(UTC).isoformat(),
            )
            self._history.append(event)
            for queue in list(self._subscribers):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)
            self._wake_notify_waiters()
        return event

    async def notify(self) -> None:
        """Wake long-poll / SSE waiters without appending history (test helper)."""
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

    async def subscribe(
        self, last_event_id: int | None = None
    ) -> AsyncGenerator[BoardEvent]:
        queue: asyncio.Queue[BoardEvent] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(queue)

            if last_event_id is not None:
                for past_event in self._history:
                    if past_event.event_id > last_event_id:
                        await queue.put(past_event)

        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            async with self._lock:
                self._subscribers.discard(queue)


# Singleton event bus instance
event_bus = EventBus()
