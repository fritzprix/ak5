from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.auth.bridge import actor_id_for_principal
from ak5.server.authz import RESERVED_ADMIN_ROLES, is_admin
from ak5.server.database import get_db
from ak5.server.models.actor import Actor
from ak5.server.models.board import Board
from ak5.server.models.board_member import BoardMember
from ak5.server.models.workspace import Agent, Membership
from ak5.server.routers.auth import get_auth_workspace_id, get_current_actor
from ak5.server.schemas.actor import ActorOut, ActorUpdate
from ak5.server.services.discovery import discover_agents

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


async def _actor_ids_in_workspace(db: AsyncSession, workspace_id: str) -> set[str]:
    """Actors visible in a workspace: members, agents, and board enrollees."""
    ids: set[str] = set()
    for (uid,) in (
        await db.execute(select(Membership.user_id).where(Membership.workspace_id == workspace_id))
    ).all():
        ids.add(actor_id_for_principal(uid))
    for (aid,) in (
        await db.execute(
            select(Agent.agent_id).where(
                Agent.workspace_id == workspace_id,
                Agent.disabled_at.is_(None),
            )
        )
    ).all():
        ids.add(actor_id_for_principal(aid))
    for (aid,) in (
        await db.execute(
            select(BoardMember.actor_id)
            .join(Board, Board.board_id == BoardMember.board_id)
            .where(Board.workspace_id == workspace_id)
        )
    ).all():
        ids.add(aid)
    return ids


@router.get("/discovery", response_model=list[ActorOut])
async def discover_available_agents(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    capability: str | None = Query(None, description="Capability tag to filter (e.g. image-resize)"),
    status: str | None = Query(None, description="Status (idle, busy, offline)"),
    query: str | None = Query(None, description="Semantic or keyword query"),
) -> list[ActorOut]:
    """Discover available AI agents in the caller's workspace. Requires Bearer JWT."""
    _ = current_actor
    allowed = await _actor_ids_in_workspace(db, workspace_id)
    agents = await discover_agents(db, capability=capability, status=status, query=query)
    return [_to_actor_out(a) for a in agents if a.actor_id in allowed]


@router.get("", response_model=list[ActorOut])
async def list_actors(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    actor_type: str | None = Query(None, description="Filter by actor_type ('human' or 'agent')"),
) -> list[ActorOut]:
    """List actors in the caller's workspace. Requires Bearer JWT."""
    _ = current_actor
    allowed = await _actor_ids_in_workspace(db, workspace_id)
    if not allowed:
        return []
    stmt = select(Actor).where(Actor.actor_id.in_(allowed))
    if actor_type:
        stmt = stmt.where(Actor.actor_type == actor_type)
    stmt = stmt.order_by(Actor.actor_type, Actor.name)
    result = await db.execute(stmt)
    return [_to_actor_out(a) for a in result.scalars().all()]


@router.get("/{actor_id}", response_model=None)
async def get_actor(
    actor_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
) -> ActorOut | JSONResponse:
    """Retrieve an actor by ID when visible in the caller's workspace."""
    _ = current_actor
    allowed = await _actor_ids_in_workspace(db, workspace_id)
    if actor_id not in allowed:
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.WORKSPACE_FORBIDDEN],
            content=error_body(
                ErrorCode.WORKSPACE_FORBIDDEN,
                "Actor is outside the caller's workspace",
                details={"actor_id": actor_id, "workspace_id": workspace_id},
            ),
        )
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
    """Update actor details or status (self or admin only; role escalation restricted)."""
    # 1. Authorization: Only the actor themselves or an admin can update
    if not (is_admin(current_actor) or current_actor.actor_id == actor_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you cannot modify another actor's profile",
        )

    stmt = select(Actor).where(Actor.actor_id == actor_id)
    result = await db.execute(stmt)
    actor = result.scalar_one_or_none()
    if not actor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Actor '{actor_id}' not found")

    # 2. Prevent role escalation to admin by non-admins
    if req.role is not None:
        target_role_lower = req.role.lower()
        if target_role_lower in RESERVED_ADMIN_ROLES and not is_admin(current_actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: cannot self-escalate role to administrative level",
            )
        actor.role = req.role

    if req.name is not None:
        actor.name = req.name
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
