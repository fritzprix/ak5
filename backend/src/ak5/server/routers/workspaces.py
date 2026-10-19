"""Workspace · membership · invite management (identity.md §1·§5·§6 / #34)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.auth.passkeys import presence_is_fresh, workspace_has_passkey_holder
from ak5.server.auth.principal import Principal
from ak5.server.database import get_db
from ak5.server.models.workspace import User, Workspace
from ak5.server.routers.auth import AuthContext, get_auth_context
from ak5.server.schemas.workspace import (
    AgentCreate,
    AgentCredentialIssue,
    AgentCredentialOut,
    AgentOut,
    InviteAccept,
    InviteCreate,
    InviteOut,
    MemberOut,
    MemberRoleUpdate,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
    WorkspaceUsageOut,
)
from ak5.server.services.agents import (
    agent_to_dict,
    create_agent_with_credential,
    disable_agent,
    issue_credential,
    list_agent_credentials,
    list_workspace_agents,
    parse_json_list,
    revoke_credential,
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
        content=error_body(exc.code, exc.message, details=getattr(exc, "details", {})),
    )


def _require_user(ctx: AuthContext) -> Principal:
    if ctx.principal is None or ctx.principal.principal_type != "user":
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN,
            "User principal required (agents cannot manage workspaces)",
        )
    return ctx.principal


async def _require_presence_to_relax_approval_policy(
    db: AsyncSession,
    principal: Principal,
    workspace: Workspace,
    policy_patch: dict | None,
) -> None:
    """Turning ``require_user_presence_for_approval`` off needs a fresh passkey assertion (#102).

    Otherwise a user token without presence could disable the policy and then approve. The
    policy is workspace-wide, so the only exception is a workspace where *nobody* holds a
    passkey: then nobody could approve anyway and this is the recovery path. A member without
    their own passkey gets no exception while another member relies on one.
    """
    if not policy_patch or policy_patch.get("require_user_presence_for_approval") is not False:
        return
    if not parse_policy(workspace.policy).get("require_user_presence_for_approval"):
        return
    if presence_is_fresh(principal.presence_at):
        return
    if not await workspace_has_passkey_holder(db, workspace.workspace_id):
        return
    raise WorkspaceServiceError(
        ErrorCode.USER_PRESENCE_REQUIRED,
        "Turning off passkey approval requires a recent passkey confirmation",
    )


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
        await _require_presence_to_relax_approval_policy(db, principal, ws, policy_patch)
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


@router.get("/{workspace_id}/usage", response_model=None)
async def get_workspace_usage_endpoint(
    workspace_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceUsageOut | JSONResponse:
    """Retrieve workspace resource quota usage (operations.md §3, #116)."""
    try:
        principal = _require_user(ctx)
        await require_membership(db, workspace_id=workspace_id, user_id=principal.principal_id)
        ws = await db.get(Workspace, workspace_id)
        if ws is None:
            raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Workspace not found")
        from ak5.server.quota import get_workspace_usage_summary

        summary = await get_workspace_usage_summary(db, ws)
        return WorkspaceUsageOut(workspace_id=ws.workspace_id, usage=summary)
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


def _agent_out(payload: dict) -> AgentOut:
    creds = [
        AgentCredentialOut(
            credential_id=c["credential_id"],
            board_scope=list(c.get("board_scope") or []),
            expires_at=c.get("expires_at"),
            revoked_at=c.get("revoked_at"),
            last_used_at=c.get("last_used_at"),
            access_token=c.get("access_token"),
        )
        for c in payload.get("credentials") or []
    ]
    return AgentOut(
        agent_id=payload["agent_id"],
        workspace_id=payload["workspace_id"],
        name=payload["name"],
        workflow_role=payload["workflow_role"],
        capabilities=list(payload.get("capabilities") or []),
        board_scope=list(payload.get("board_scope") or []),
        created_by=payload["created_by"],
        disabled_at=payload.get("disabled_at"),
        credentials=creds,
    )


@router.get("/{workspace_id}/agents", response_model=None)
async def get_agents(
    workspace_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AgentOut] | JSONResponse:
    try:
        principal = _require_user(ctx)
        await require_membership(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        agents = await list_workspace_agents(db, workspace_id)
        out: list[AgentOut] = []
        for agent in agents:
            creds = await list_agent_credentials(db, agent.agent_id)
            out.append(_agent_out(agent_to_dict(agent, creds)))
        return out
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.post("/{workspace_id}/agents", response_model=None)
async def post_agent(
    workspace_id: str,
    body: AgentCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentOut | JSONResponse:
    """Create agent and issue first opaque credential (identity.md §4.2)."""
    try:
        principal = _require_user(ctx)
        await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        if await db.get(Workspace, workspace_id) is None:
            raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Workspace not found")
        agent, cred, plaintext = await create_agent_with_credential(
            db,
            workspace_id=workspace_id,
            name=body.name,
            workflow_role=body.workflow_role,
            created_by=principal.principal_id,
            capabilities=body.capabilities,
            board_scope=body.board_scope,
        )
        await db.commit()
        payload = agent_to_dict(agent, [cred])
        payload["credentials"][0]["access_token"] = plaintext
        return _agent_out(payload)
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.post("/{workspace_id}/agents/{agent_id}/credentials", response_model=None)
async def post_agent_credential(
    workspace_id: str,
    agent_id: str,
    body: AgentCredentialIssue,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentCredentialOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        cred, plaintext = await issue_credential(
            db,
            workspace_id=workspace_id,
            agent_id=agent_id,
            board_scope=body.board_scope,
        )
        await db.commit()
        return AgentCredentialOut(
            credential_id=cred.credential_id,
            board_scope=parse_json_list(cred.board_scope),
            expires_at=cred.expires_at,
            revoked_at=cred.revoked_at,
            last_used_at=cred.last_used_at,
            access_token=plaintext,
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.post(
    "/{workspace_id}/agents/{agent_id}/credentials/{credential_id}/revoke",
    response_model=None,
)
async def post_revoke_agent_credential(
    workspace_id: str,
    agent_id: str,
    credential_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentCredentialOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        cred = await revoke_credential(
            db,
            workspace_id=workspace_id,
            agent_id=agent_id,
            credential_id=credential_id,
        )
        await db.commit()
        return AgentCredentialOut(
            credential_id=cred.credential_id,
            board_scope=parse_json_list(cred.board_scope),
            expires_at=cred.expires_at,
            revoked_at=cred.revoked_at,
            last_used_at=cred.last_used_at,
        )
    except WorkspaceServiceError as exc:
        return _err(exc)


@router.post("/{workspace_id}/agents/{agent_id}/disable", response_model=None)
async def post_disable_agent(
    workspace_id: str,
    agent_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentOut | JSONResponse:
    try:
        principal = _require_user(ctx)
        await require_workspace_admin(
            db, workspace_id=workspace_id, user_id=principal.principal_id
        )
        agent = await disable_agent(
            db, workspace_id=workspace_id, agent_id=agent_id
        )
        await db.commit()
        creds = await list_agent_credentials(db, agent.agent_id)
        return _agent_out(agent_to_dict(agent, creds))
    except WorkspaceServiceError as exc:
        return _err(exc)
