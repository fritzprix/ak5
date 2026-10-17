import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.authz import is_admin, is_admin_or_owner
from ak5.server.config import settings
from ak5.server.database import get_db
from ak5.server.models.actor import Actor
from ak5.server.models.event import Event
from ak5.server.models.subscription import Subscription
from ak5.server.routers.auth import get_current_actor
from ak5.server.schemas.subscription import (
    LongPollEventsOut,
    SubscriptionAckIn,
    SubscriptionAckOut,
    SubscriptionConsumer,
    SubscriptionCreate,
    SubscriptionFilter,
    SubscriptionOut,
)
from ak5.server.services.event_context import normalize_actor_id
from ak5.server.services.subscription_service import SubscriptionServiceError, subscription_service

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _error_response(exc: SubscriptionServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[exc.code],
        content=error_body(exc.code, exc.message, details=exc.details),
    )


def _caps_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, list):
        return [str(c) for c in parsed]
    return None


def _to_subscription_out(s: Subscription) -> SubscriptionOut:
    events = [e.strip() for e in s.events.split(",") if e.strip()] if s.events else None
    consumer = None
    if s.consumer_id and s.consumer_lease_expires_at:
        consumer = SubscriptionConsumer(
            consumer_id=s.consumer_id,
            lease_expires_at=s.consumer_lease_expires_at,
        )
    return SubscriptionOut(
        subscription_id=s.subscription_id,
        owner=s.created_by,
        filter=SubscriptionFilter(
            board_id=s.board_id,
            events=events,
            role=s.role,
            capabilities=_caps_list(s.capabilities),
            for_actor=s.for_agent,
        ),
        ack_cursor=s.ack_cursor,
        consumer=consumer,
        created_at=s.created_at,
    )


def _require_owned_sub(
    sub: Subscription | None,
    sub_id: str,
    current_actor: Actor,
) -> Subscription:
    if not sub:
        raise HTTPException(
            status_code=HTTP_STATUS[ErrorCode.SUBSCRIPTION_NOT_FOUND],
            detail=error_body(
                ErrorCode.SUBSCRIPTION_NOT_FOUND,
                f"Subscription '{sub_id}' not found",
            )["error"],
        )
    if not is_admin_or_owner(current_actor, sub.created_by):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this subscription",
        )
    return sub


@router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    sub_in: SubscriptionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
) -> SubscriptionOut:
    """Register a filter-only subscription (events.md §2). Hooks run on the client daemon."""
    sub_id = sub_in.subscription_id or f"sub-{uuid.uuid4().hex[:8]}"

    existing = await db.get(Subscription, sub_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subscription with ID '{sub_id}' already exists",
        )

    filt = sub_in.filter
    events_str = None
    if filt.events:
        events_str = ",".join(e.strip().upper() for e in filt.events if e.strip())

    caps_json = None
    if filt.capabilities:
        caps_json = json.dumps([str(c) for c in filt.capabilities])

    # Default start="now": ack at current tip so daemons do not replay retention history.
    if sub_in.start == "earliest":
        ack_cursor = 0
    else:
        max_seq = (await db.execute(select(func.max(Event.seq)))).scalar_one_or_none()
        ack_cursor = int(max_seq or 0)

    subscription = Subscription(
        subscription_id=sub_id,
        board_id=filt.board_id,
        events=events_str,
        role=filt.role,
        capabilities=caps_json,
        for_agent=normalize_actor_id(filt.for_actor) if filt.for_actor else None,
        created_by=current_actor.actor_id,
        ack_cursor=ack_cursor,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return _to_subscription_out(subscription)


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    board_id: Annotated[str | None, Query(description="Filter by board ID")] = None,
) -> list[SubscriptionOut]:
    """List subscriptions owned by the current actor (admins see all)."""
    stmt = select(Subscription)
    if not is_admin(current_actor):
        stmt = stmt.where(Subscription.created_by == current_actor.actor_id)

    if board_id:
        stmt = stmt.where(Subscription.board_id == board_id)

    result = await db.execute(stmt)
    subscriptions = result.scalars().all()
    return [_to_subscription_out(s) for s in subscriptions]


@router.get("/{sub_id}", response_model=SubscriptionOut)
async def get_subscription(
    sub_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
) -> SubscriptionOut:
    """Retrieve a subscription (owner or admin)."""
    sub = _require_owned_sub(await db.get(Subscription, sub_id), sub_id, current_actor)
    return _to_subscription_out(sub)


@router.delete("/{sub_id}", status_code=status.HTTP_200_OK)
async def delete_subscription(
    sub_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
) -> dict[str, str]:
    """Delete a subscription (owner or admin)."""
    sub = _require_owned_sub(await db.get(Subscription, sub_id), sub_id, current_actor)
    await db.delete(sub)
    await db.commit()
    return {"status": "unsubscribed", "subscription_id": sub_id}


@router.get("/{sub_id}/events", response_model=None)
async def long_poll_subscription_events(
    sub_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    x_ak5_consumer: Annotated[str | None, Header(alias="X-AK5-Consumer")] = None,
    cursor: Annotated[int | None, Query(ge=0, description="Resume after this seq")] = None,
    wait: Annotated[int, Query(ge=0, description="Seconds to wait when empty")] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> LongPollEventsOut | JSONResponse:
    """Long-poll subscription events (events.md §3)."""
    sub = _require_owned_sub(await db.get(Subscription, sub_id), sub_id, current_actor)

    if not x_ak5_consumer or not x_ak5_consumer.strip():
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.PAYLOAD_INVALID],
            content=error_body(
                ErrorCode.PAYLOAD_INVALID,
                "X-AK5-Consumer header is required",
            ),
        )

    wait = min(wait, settings.LONG_POLL_MAX_WAIT)

    try:
        events, next_cursor = await subscription_service.long_poll_events(
            sub=sub,
            cursor=cursor,
            wait=wait,
            limit=limit,
            consumer_id=x_ak5_consumer,
        )
    except SubscriptionServiceError as exc:
        return _error_response(exc)

    return LongPollEventsOut(events=events, next_cursor=next_cursor)


@router.post("/{sub_id}/ack", response_model=None)
async def ack_subscription(
    sub_id: str,
    body: SubscriptionAckIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    x_ak5_consumer: Annotated[str | None, Header(alias="X-AK5-Consumer")] = None,
) -> SubscriptionAckOut | JSONResponse:
    """Advance the server-stored ack cursor (monotonic; events.md §3.2)."""
    sub = _require_owned_sub(await db.get(Subscription, sub_id), sub_id, current_actor)

    try:
        ack = await subscription_service.ack_cursor(
            db,
            sub,
            body.cursor,
            consumer_id=x_ak5_consumer,
        )
    except SubscriptionServiceError as exc:
        return _error_response(exc)

    return SubscriptionAckOut(ack_cursor=ack)
