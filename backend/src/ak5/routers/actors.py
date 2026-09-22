from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.routers.auth import get_current_actor
from ak5.schemas.actor import ActorOut, ActorUpdate
from ak5.services.discovery import discover_agents

router = APIRouter(prefix="/actors", tags=["actors"])


def _to_actor_out(actor: Actor) -> ActorOut:
    return ActorOut(
        actor_id=actor.actor_id,
        actor_type=actor.actor_type,
        name=actor.name,
        role=actor.role,
        description=actor.description,
        capabilities=actor.capability_list,
        status=actor.status,
        avatar_url=actor.avatar_url,
        created_at=actor.created_at,
        updated_at=actor.updated_at,
    )


@router.get("/discovery", response_model=list[ActorOut])
async def discover_available_agents(
    db: Annotated[AsyncSession, Depends(get_db)],
    capability: str | None = Query(None, description="Capability tag to filter (e.g. image-resize)"),
    status: str | None = Query(None, description="Status (idle, busy, offline)"),
    query: str | None = Query(None, description="Semantic or keyword query"),
) -> list[ActorOut]:
    """Discover available AI agents based on capability tags, status, and search query."""
    agents = await discover_agents(db, capability=capability, status=status, query=query)
    return [_to_actor_out(a) for a in agents]


@router.get("", response_model=list[ActorOut])
async def list_actors(
    db: Annotated[AsyncSession, Depends(get_db)],
    actor_type: str | None = Query(None, description="Filter by actor_type ('human' or 'agent')"),
) -> list[ActorOut]:
    """List all registered actors."""
    stmt = select(Actor)
    if actor_type:
        stmt = stmt.where(Actor.actor_type == actor_type)
    stmt = stmt.order_by(Actor.actor_type, Actor.name)
    result = await db.execute(stmt)
    actors = result.scalars().all()
    return [_to_actor_out(a) for a in actors]


@router.get("/{actor_id}", response_model=ActorOut)
async def get_actor(
    actor_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ActorOut:
    """Retrieve an actor by ID."""
    stmt = select(Actor).where(Actor.actor_id == actor_id)
    result = await db.execute(stmt)
    actor = result.scalar_one_or_none()
    if not actor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Actor '{actor_id}' not found")
    return _to_actor_out(actor)


@router.patch("/{actor_id}", response_model=ActorOut)
async def update_actor(
    actor_id: str,
    req: ActorUpdate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ActorOut:
    """Update actor details or status."""
    stmt = select(Actor).where(Actor.actor_id == actor_id)
    result = await db.execute(stmt)
    actor = result.scalar_one_or_none()
    if not actor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Actor '{actor_id}' not found")

    if req.name is not None:
        actor.name = req.name
    if req.role is not None:
        actor.role = req.role
    if req.description is not None:
        actor.description = req.description
    if req.capabilities is not None:
        actor.capability_list = req.capabilities
    if req.status is not None:
        actor.status = req.status
    if req.avatar_url is not None:
        actor.avatar_url = req.avatar_url

    await db.commit()
    await db.refresh(actor)
    return _to_actor_out(actor)
