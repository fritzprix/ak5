import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any, AsyncGenerator


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
        self._lock = asyncio.Lock()

    async def publish(self, event_type: str, data: dict[str, Any]) -> BoardEvent:
        async with self._lock:
            self._counter += 1
            event = BoardEvent(
                event_id=self._counter,
                event_type=event_type,
                data=data,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self._history.append(event)
            # Dispatch to all active subscribers
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        return event

    async def subscribe(
        self, last_event_id: int | None = None
    ) -> AsyncGenerator[BoardEvent, None]:
        queue: asyncio.Queue[BoardEvent] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(queue)

            # Replay missed events if requested
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
