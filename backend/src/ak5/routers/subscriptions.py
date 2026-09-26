import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.authz import is_admin, is_admin_or_owner
from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.models.subscription import Subscription
from ak5.routers.auth import get_current_actor
from ak5.schemas.subscription import SubscriptionCreate, SubscriptionOut

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    sub_in: SubscriptionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
) -> SubscriptionOut:
    """Register a new server-side event subscription hook."""
    sub_id = sub_in.subscription_id or f"sub-{uuid.uuid4().hex[:8]}"

    # Check for duplicate ID
    existing = await db.get(Subscription, sub_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subscription with ID '{sub_id}' already exists",
        )

    # Format events to comma-separated string if provided as list
    events_str = None
    if sub_in.events:
        if isinstance(sub_in.events, list):
            events_str = ",".join(e.strip().upper() for e in sub_in.events if e.strip())
        else:
            events_str = sub_in.events.strip().upper()

    subscription = Subscription(
        subscription_id=sub_id,
        board_id=sub_in.board_id,
        exec_command=sub_in.exec_command,
        events=events_str,
        for_agent=sub_in.for_agent,
        ignore_actor=sub_in.ignore_actor,
        debounce_seconds=sub_in.debounce_seconds,
        created_by=current_actor.actor_id,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)

    # Convert to response schema
    return _to_subscription_out(subscription)


def _to_subscription_out(s: Subscription) -> SubscriptionOut:
    """Convert Subscription model to SubscriptionOut schema."""
    return SubscriptionOut(
        subscription_id=s.subscription_id,
        board_id=s.board_id,
        exec_command=s.exec_command,
        events=[e.strip() for e in s.events.split(",")] if s.events else None,
        for_agent=s.for_agent,
        ignore_actor=s.ignore_actor,
        debounce_seconds=s.debounce_seconds,
        created_by=s.created_by,
        created_at=s.created_at,
    )


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    board_id: Annotated[str | None, Query(description="Filter by board ID")] = None,
) -> list[SubscriptionOut]:
    """List registered server-side event subscriptions (scoped to current actor unless admin)."""
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
    """Retrieve details of a specific subscription (creator or admin only)."""
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription '{sub_id}' not found",
        )
    if not is_admin_or_owner(current_actor, sub.created_by):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this subscription hook",
        )
    return _to_subscription_out(sub)


@router.delete("/{sub_id}", status_code=status.HTTP_200_OK)
async def delete_subscription(
    sub_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
) -> dict[str, str]:
    """Unsubscribe / delete an event subscription hook (creator or admin only)."""
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription '{sub_id}' not found",
        )
    if not is_admin_or_owner(current_actor, sub.created_by):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this subscription hook",
        )
    await db.delete(sub)
    await db.commit()
    return {"status": "unsubscribed", "subscription_id": sub_id}
