import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.config import settings
from ak5.server.database import get_db
from ak5.server.inbox import resolve_board_alias
from ak5.server.models.actor import Actor
from ak5.server.routers.auth import get_auth_workspace_id, get_current_actor
from ak5.server.schemas.event import EventOut, EventsPollOut
from ak5.server.security import get_jwt_secret
from ak5.server.services.event_bus import event_bus
from ak5.server.services.subscription_service import SubscriptionServiceError, subscription_service

router = APIRouter(prefix="/events", tags=["events"])
_security = HTTPBearer(auto_error=False)


async def _actor_from_bearer(
    db: AsyncSession,
    credentials: HTTPAuthorizationCredentials | None,
) -> Actor:
    """Bearer header only: clients stream with fetch, so no ``?access_token=`` (#81)."""
    token = credentials.credentials if credentials else None
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer header required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[settings.JWT_ALGORITHM])
        actor_id = payload.get("sub")
        if not actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    actor = (await db.execute(select(Actor).where(Actor.actor_id == actor_id))).scalar_one_or_none()
    if not actor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Actor '{actor_id}' not found")
    return actor


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


@router.get("/stream")
async def sse_event_stream(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_security)],
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
) -> EventSourceResponse:
    """SSE observation stream sourced from the persistent event log (events.md §6)."""
    await _actor_from_bearer(db, credentials)

    # Without Last-Event-ID / last_event_id, start at tip — do not replay retention history.
    tip_seq = await subscription_service.max_event_seq(db)
    resume_seq = last_event_id_header if last_event_id_header is not None else last_event_id

    async def event_generator() -> AsyncGenerator[dict]:
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

        try:
            while True:
                if await request.is_disconnected():
                    break

                async with subscription_service.session_factory() as session:
                    try:
                        rows, next_cursor = await subscription_service.poll_events(
                            session,
                            board_id=board_id,
                            since=last_seq,
                            limit=100,
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
                            timeout = (
                                settings.LONG_POLL_DB_POLL_INTERVAL
                                if not settings.LONG_POLL_USE_INPROCESS_NOTIFY
                                else max(settings.LONG_POLL_DB_POLL_INTERVAL, 15.0)
                            )
                            await event_bus.wait_for_notify(timeout)
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

                timeout = (
                    settings.LONG_POLL_DB_POLL_INTERVAL
                    if not settings.LONG_POLL_USE_INPROCESS_NOTIFY
                    else max(settings.LONG_POLL_DB_POLL_INTERVAL, 15.0)
                )
                await event_bus.wait_for_notify(timeout)
        except asyncio.CancelledError:
            return

    return EventSourceResponse(event_generator())
