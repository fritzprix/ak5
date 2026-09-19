import asyncio
import json
from typing import AsyncGenerator
from fastapi import APIRouter, Header, Request
from sse_starlette.sse import EventSourceResponse

from ak5.services.event_bus import event_bus

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def sse_event_stream(
    request: Request,
    last_event_id: int | None = Header(None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Server-Sent Events (SSE) stream for real-time board updates.

    Emits TICKET_CREATED, TICKET_MOVED, TICKET_DELEGATED, TICKET_UPDATED, COMMENT_ADDED events.
    """
    async def event_generator() -> AsyncGenerator[dict, None]:
        # Send initial ping event
        yield {
            "event": "CONNECTED",
            "data": json.dumps({"status": "connected", "message": "AK5 Event Stream Active"}),
        }

        async for board_event in event_bus.subscribe(last_event_id=last_event_id):
            if await request.is_disconnected():
                break
            yield {
                "id": str(board_event.event_id),
                "event": board_event.event_type,
                "data": json.dumps(board_event.data),
            }

    return EventSourceResponse(event_generator())
