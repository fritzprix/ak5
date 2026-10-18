"""Workspace · membership · invite management (identity.md §1·§5·§6 / #34)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.auth.principal import Principal
from ak5.server.database import get_db
from ak5.server.models.workspace import User, Workspace
from ak5.server.routers.auth import AuthContext, get_auth_context
from ak5.server.schemas.workspace import (
    InviteAccept,
    InviteCreate,
    InviteOut,
    MemberOut,
    MemberRoleUpdate,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from ak5.server.services.workspaces import (
    WorkspaceServiceError,
    accept_invite,
    create_invite,
    create_workspace,
    inbox_board_id_for,
    list_members,
    list_workspaces_for_user,
    parse_policy,
    remove_member,
    require_membership,
    require_workspace_admin,
    update_member_role,
    update_workspace,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _err(exc: WorkspaceServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[exc.code],
        content=error_body(exc.code, exc.message),
    )


def _require_user(ctx: AuthContext) -> Principal:
    if ctx.principal is None or ctx.principal.principal_type != "user":
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN,
            "User principal required (agents cannot manage workspaces)",
        )
    return ctx.principal


async def _load_user(db: AsyncSession, user_id: str) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "User not found")
    return user


@router.post("", response_model=None)
async def post_workspace(
    body: WorkspaceCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        user = await _load_user(db, principal.principal_id)
        ws, inbox_id = await create_workspace(db, name=body.name, owner=user)
        await db.commit()
        return WorkspaceOut(
            workspace_id=ws.workspace_id,
            name=ws.name,
            policy=parse_policy(ws.policy),
            inbox_board_id=inbox_id,
            membership_role="owner",
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.get("", response_model=None)
async def get_workspaces(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[WorkspaceOut] | JSONResponse:
    try:
        principal = _require_user(ctx)
        rows = await list_workspaces_for_user(db, principal.principal_id)
        out: list[WorkspaceOut] = []
        for ws in rows:
            mem = await require_membership(
                db, workspace_id=ws.workspace_id, user_id=principal.principal_id
            )
            out.append(
                WorkspaceOut(
                    workspace_id=ws.workspace_id,
                    name=ws.name,
                    policy=parse_policy(ws.policy),
                    membership_role=mem.role,
                )
            )
        return out
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.post("/invites/accept", response_model=None)
async def post_accept_invite(
    body: InviteAccept,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MemberOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        user = await _load_user(db, principal.principal_id)
        membership = await accept_invite(db, token=body.token, user=user)
        await db.commit()
        return MemberOut(
            user_id=user.user_id,
            email=user.email,
            display_name=user.display_name,
            role=membership.role,
            membership_id=membership.membership_id,
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.get("/{workspace_id}", response_model=None)
async def get_workspace(
    workspace_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        mem = await require_membership(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        ws = await db.get(Workspace, workspace_id)
        if ws is None:
            raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Workspace not found")
        return WorkspaceOut(
            workspace_id=ws.workspace_id,
            name=ws.name,
            policy=parse_policy(ws.policy),
            inbox_board_id=inbox_board_id_for(ws.workspace_id),
            membership_role=mem.role,
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.patch("/{workspace_id}", response_model=None)
async def patch_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        ws = await db.get(Workspace, workspace_id)
        if ws is None:
            raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Workspace not found")
        policy_patch = body.policy.model_dump(exclude_none=True) if body.policy else None
        ws = await update_workspace(
            db,
            workspace=ws,
            actor_user_id=principal.principal_id,
            name=body.name,
            policy_patch=policy_patch,
        )
        await db.commit()
        mem = await require_membership(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        return WorkspaceOut(
            workspace_id=ws.workspace_id,
            name=ws.name,
            policy=parse_policy(ws.policy),
            inbox_board_id=inbox_board_id_for(ws.workspace_id),
            membership_role=mem.role,
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.get("/{workspace_id}/members", response_model=None)
async def get_members(
    workspace_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MemberOut] | JSONResponse:
    try:
        principal = _require_user(ctx)
        await require_membership(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        rows = await list_members(db, workspace_id)
        return [MemberOut(**row) for row in rows]
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.post("/{workspace_id}/members", response_model=None)
async def post_member_invite(
    workspace_id: str,
    body: InviteCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InviteOut | JSONResponse:
    """Create an email invite (identity.md §1). Token returned once."""
    try:
        principal = _require_user(ctx)
        await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        if await db.get(Workspace, workspace_id) is None:
            raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Workspace not found")
        invite, token = await create_invite(
            db,
            workspace_id=workspace_id,
            email=body.email,
            role=body.role,
            invited_by=principal.principal_id,
        )
        await db.commit()
        return InviteOut(
            invite_id=invite.invite_id,
            workspace_id=invite.workspace_id,
            email=invite.email,
            role=invite.role,
            expires_at=invite.expires_at,
            token=token,
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.patch("/{workspace_id}/members/{user_id}", response_model=None)
async def patch_member(
    workspace_id: str,
    user_id: str,
    body: MemberRoleUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MemberOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        actor = await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        row = await update_member_role(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            new_role=body.role,
            actor_role=actor.role,
        )
        await db.commit()
        user = await _load_user(db, user_id)
        return MemberOut(
            user_id=user.user_id,
            email=user.email,
            display_name=user.display_name,
            role=row.role,
            membership_id=row.membership_id,
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.delete("/{workspace_id}/members/{user_id}", response_model=None)
async def delete_member(
    workspace_id: str,
    user_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response | JSONResponse:
    try:
        principal = _require_user(ctx)
        actor = await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        await remove_member(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            actor_role=actor.role,
        )
        await db.commit()
        return Response(status_code=204)
    except WorkspaceServiceError as exc:
        return _err(exc)
