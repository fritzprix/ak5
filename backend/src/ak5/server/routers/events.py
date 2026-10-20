import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.config import get_notify_mode, settings
from ak5.server.connection_limit import ConnectionLimitExceeded, connection_limiter
from ak5.server.database import get_db
from ak5.server.inbox import resolve_board_alias
from ak5.server.models.actor import Actor
from ak5.server.models.board import Board
from ak5.server.routers.auth import get_auth_workspace_id, get_current_actor
from ak5.server.schemas.event import EventOut, EventsPollOut
from ak5.server.services.notify import wait_for_wake, wake_wait_slice
from ak5.server.services.subscription_service import SubscriptionServiceError, subscription_service

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=None)
async def poll_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    board_id: Annotated[str | None, Query(description="Filter by board")] = None,
    since: Annotated[int, Query(ge=0, description="Return events with seq > since")] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EventsPollOut | JSONResponse:
    """One-shot event log poll without a subscription (cli.md ``ak5 events poll``)."""
    _ = current_actor
    if board_id:
        board_id = resolve_board_alias(board_id, workspace_id)
    try:
        events, next_cursor = await subscription_service.poll_events(
            db,
            board_id=board_id,
            since=since,
            limit=limit,
            workspace_id=workspace_id,
        )
    except SubscriptionServiceError as exc:
        return JSONResponse(
            status_code=HTTP_STATUS[exc.code],
            content=error_body(exc.code, exc.message, details=exc.details),
        )
    await db.commit()
    return EventsPollOut(
        events=[EventOut.model_validate(e) for e in events],
        next_cursor=next_cursor,
    )


@router.get("/stream", response_model=None)
async def sse_event_stream(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    board_id: Annotated[
        str | None, Query(description="Limit stream to one board (Web UI / observation)")
    ] = None,
    last_event_id_header: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
    last_event_id: Annotated[
        int | None,
        Query(
            description="Resume cursor when EventSource reconnects without Last-Event-ID header"
        ),
    ] = None,
) -> EventSourceResponse | JSONResponse:
    """SSE observation stream sourced from the persistent event log (events.md §6).

    Auth goes through the same Principal verification as every other API route
    (membership, revocation, disabled agents) and the stream only ever reads the
    caller's workspace (#84).
    """
    _ = current_actor
    if board_id:
        board = await db.get(Board, board_id)
        if board is None or board.workspace_id != workspace_id:
            return JSONResponse(
                status_code=HTTP_STATUS[ErrorCode.WORKSPACE_FORBIDDEN],
                content=error_body(
                    ErrorCode.WORKSPACE_FORBIDDEN,
                    "Board is outside the caller's workspace",
                    details={"board_id": board_id, "workspace_id": workspace_id},
                ),
            )

    # Without Last-Event-ID / last_event_id, start at tip — do not replay retention history.
    tip_seq = await subscription_service.max_event_seq(db)
    resume_seq = last_event_id_header if last_event_id_header is not None else last_event_id

    try:
        slot = connection_limiter.acquire(current_actor.actor_id, workspace_id, kind="sse")
    except ConnectionLimitExceeded as exc:
        return exc.response()

    async def event_generator() -> AsyncGenerator[dict]:
        try:
            yield {
                "event": "CONNECTED",
                "data": json.dumps(
                    {
                        "status": "connected",
                        "message": "AK5 Event Stream Active",
                        "board_id": board_id,
                    }
                ),
            }

            last_seq = int(resume_seq) if resume_seq is not None else tip_seq

            while True:
                if await request.is_disconnected():
                    break

                async with subscription_service.session_factory() as session:
                    try:
                        from ak5.server.rls import set_workspace_rls_context

                        await set_workspace_rls_context(session, workspace_id)
                        rows, next_cursor = await subscription_service.poll_events(
                            session,
                            board_id=board_id,
                            since=last_seq,
                            limit=100,
                            workspace_id=workspace_id,
                        )
                        await session.commit()
                    except SubscriptionServiceError as exc:
                        if exc.code == ErrorCode.CURSOR_EXPIRED:
                            tip = await subscription_service.max_event_seq(session)
                            last_seq = tip
                            await session.commit()
                            yield {
                                "event": "RESYNC",
                                "data": json.dumps(
                                    {
                                        "reason": "CURSOR_EXPIRED",
                                        "cursor": tip,
                                    }
                                ),
                            }
                            # Wait for new events at the tip; do not tight-loop RESYNC.
                            mode = get_notify_mode()
                            timeout = (
                                wake_wait_slice(15.0)
                                if mode == "poll"
                                else max(settings.LONG_POLL_DB_POLL_INTERVAL, 15.0)
                            )
                            await wait_for_wake(timeout)
                            continue
                        raise

                if rows:
                    for row in rows:
                        last_seq = int(row["seq"])
                        yield {
                            "id": str(row["seq"]),
                            "event": str(row["type"]),
                            "data": json.dumps(row),
                        }
                    continue

                # Advance even when empty so visibility ceilings / gaps do not stall.
                if next_cursor > last_seq:
                    last_seq = next_cursor

                mode = get_notify_mode()
                timeout = (
                    wake_wait_slice(15.0)
                    if mode == "poll"
                    else max(settings.LONG_POLL_DB_POLL_INTERVAL, 15.0)
                )
                await wait_for_wake(timeout)
        except asyncio.CancelledError:
            return
        finally:
            slot.release()

    try:
        return EventSourceResponse(event_generator(), background=BackgroundTask(slot.release))
    except Exception:
        slot.release()
        raise
