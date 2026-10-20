from typing import Any

from pydantic import BaseModel, Field


class EventOut(BaseModel):
    """Persisted event log row for long-poll / one-shot poll (events.md §3)."""

    seq: int
    event_id: str
    type: str
    board_id: str | None = None
    ticket_id: str | None = None
    actor_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class EventsPollOut(BaseModel):
    """``GET /events`` one-shot response (events.md §5 / cli.md ``events poll``)."""

    events: list[EventOut]
    next_cursor: int


class SSEEventOut(BaseModel):
    """Legacy in-memory bus shape (kept for typing compatibility)."""

    event_id: int
    event_type: str
    data: dict[str, Any]
    timestamp: str
