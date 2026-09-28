import json
from collections.abc import AsyncGenerator
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ak5.config import settings
from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.security import get_jwt_secret
from ak5.services.event_bus import event_bus

router = APIRouter(prefix="/events", tags=["events"])
_security = HTTPBearer(auto_error=False)


async def _actor_from_bearer_or_query(
    db: AsyncSession,
    credentials: HTTPAuthorizationCredentials | None,
    access_token: str | None,
) -> Actor:
    """SSE cannot set Authorization in browsers — allow ``?access_token=`` as well."""
    token = credentials.credentials if credentials else (access_token or "").strip() or None
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header or access_token query param required",
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


@router.get("/stream")
async def sse_event_stream(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_security)],
    access_token: Annotated[str | None, Query(description="JWT for EventSource clients")] = None,
    last_event_id: int | None = Header(None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Server-Sent Events stream. Requires JWT (Bearer or ``access_token`` query)."""
    await _actor_from_bearer_or_query(db, credentials, access_token)

    async def event_generator() -> AsyncGenerator[dict]:
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
