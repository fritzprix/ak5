"""Workspace create / policy / membership / invites (identity.md §1·§5·§6 / #34)."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import ErrorCode
from ak5.server.auth.bridge import actor_id_for_principal
from ak5.server.inbox import inbox_board_id_for  # noqa: F401  (re-exported for routers)
from ak5.server.models.actor import Actor
from ak5.server.models.audit import AuditLog
from ak5.server.models.board import Board
from ak5.server.models.board_member import BoardMember
from ak5.server.models.column import Column
from ak5.server.models.workspace import (
    Membership,
    User,
    UserIdentity,
    Workspace,
)
from ak5.server.models.workspace_invite import WorkspaceInvite
from ak5.server.quota import (
    QUOTA_KEYS,
    QuotaExceeded,
    check_member_quota,
    validate_quota_policy_patch,
)
from ak5.server.seed import LOCAL_USER_ID
from ak5.server.services.templates import INBOX_TEMPLATE

DEFAULT_POLICY: dict[str, Any] = {
    "require_user_presence_for_approval": False,
    "allowed_idp_issuers": [],
    "allowed_email_domains": [],
    "max_auto_boards": 50,
}

_ADMIN_ROLES = frozenset({"owner", "admin"})
_MEMBER_ROLES = frozenset({"owner", "admin", "member"})
_INVITE_ROLES = frozenset({"admin", "member"})


class WorkspaceServiceError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def default_policy_json() -> str:
    return json.dumps(DEFAULT_POLICY)


def parse_policy(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return dict(DEFAULT_POLICY)
    if not isinstance(data, dict):
        return dict(DEFAULT_POLICY)
    merged = dict(DEFAULT_POLICY)
    for key, val in data.items():
        if key in DEFAULT_POLICY or key in QUOTA_KEYS:
            merged[key] = val
    return merged


def merge_policy(existing: str, patch: dict[str, Any]) -> str:
    current = parse_policy(existing)
    for key, value in patch.items():
        if key in DEFAULT_POLICY or key in QUOTA_KEYS:
            current[key] = value
    return json.dumps(current)




def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def get_membership(
    db: AsyncSession, *, workspace_id: str, user_id: str
) -> Membership | None:
    return (
        await db.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def require_membership(
    db: AsyncSession, *, workspace_id: str, user_id: str
) -> Membership:
    row = await get_membership(db, workspace_id=workspace_id, user_id=user_id)
    if row is None:
        raise WorkspaceServiceError(
            ErrorCode.WORKSPACE_FORBIDDEN, "Not a member of this workspace"
        )
    return row


async def require_workspace_admin(
    db: AsyncSession, *, workspace_id: str, user_id: str
) -> Membership:
    row = await require_membership(db, workspace_id=workspace_id, user_id=user_id)
    if row.role not in _ADMIN_ROLES:
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN, "Workspace admin or owner required"
        )
    return row


async def provision_inbox_board(
    db: AsyncSession, *, workspace_id: str, created_by: str
) -> str:
    """Create the workspace inbox board + columns (workflow.md §2.1)."""
    board_id = inbox_board_id_for(workspace_id)
    existing = await db.get(Board, board_id)
    if existing is not None:
        return board_id
    actor_id = actor_id_for_principal(created_by)
    db.add(
        Board(
            board_id=board_id,
            workspace_id=workspace_id,
            name="Inbox",
            description="Workspace inbox for unclassified work",
            created_by=actor_id,
            kind="inbox",
            template=INBOX_TEMPLATE.name,
            auto_created=False,
        )
    )
    for col in INBOX_TEMPLATE.columns:
        db.add(
            Column(
                column_id=f"{board_id}_{col.stage}",
                board_id=board_id,
                name=col.name,
                stage=col.stage,
                position=col.position,
                wip_limit=col.wip_limit,
            )
        )
    if await db.get(Actor, actor_id) is not None:
        db.add(
            BoardMember(
                board_id=board_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                role="admin",
            )
        )
    await db.flush()
    return board_id


async def ensure_user_actor(db: AsyncSession, user: User) -> str:
    """Ensure an Actor row exists for audit FK / board membership. Return actor_id."""
    actor_id = actor_id_for_principal(user.user_id)
    if await db.get(Actor, actor_id) is None:
        db.add(
            Actor(
                actor_id=actor_id,
                actor_type="human",
                name=user.display_name or user.user_id,
                role="human",
                capabilities="[]",
                status="idle",
            )
        )
        await db.flush()
    return actor_id


async def create_workspace(
    db: AsyncSession, *, name: str, owner: User
) -> tuple[Workspace, str]:
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    ws = Workspace(
        workspace_id=workspace_id,
        name=name.strip() or workspace_id,
        policy=default_policy_json(),
    )
    db.add(ws)
    await ensure_user_actor(db, owner)
    db.add(
        Membership(
            membership_id=f"mem_{workspace_id}_{owner.user_id}",
            workspace_id=workspace_id,
            user_id=owner.user_id,
            role="owner",
        )
    )
    inbox_id = await provision_inbox_board(
        db, workspace_id=workspace_id, created_by=owner.user_id
    )
    await db.flush()
    return ws, inbox_id


async def list_workspaces_for_user(db: AsyncSession, user_id: str) -> list[Workspace]:
    rows = (
        await db.execute(
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.workspace_id)
            .where(Membership.user_id == user_id)
            .order_by(Workspace.name.asc())
        )
    ).scalars().all()
    return list(rows)


async def update_workspace(
    db: AsyncSession,
    *,
    workspace: Workspace,
    actor_user_id: str,
    name: str | None = None,
    policy_patch: dict[str, Any] | None = None,
) -> Workspace:
    before_policy = parse_policy(workspace.policy)
    if name is not None and name.strip():
        workspace.name = name.strip()
    if policy_patch is not None:
        try:
            validate_quota_policy_patch(policy_patch)
        except ValueError as exc:
            raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, str(exc)) from exc

        workspace.policy = merge_policy(workspace.policy, policy_patch)
        after_policy = parse_policy(workspace.policy)
        user = await db.get(User, actor_user_id)
        if user is None:
            raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "User not found")
        actor_id = await ensure_user_actor(db, user)
        db.add(
            AuditLog(
                workspace_id=workspace.workspace_id,
                actor_id=actor_id,
                action="WORKSPACE_POLICY_UPDATED",
                target_type="workspace",
                target_id=workspace.workspace_id,
                payload=json.dumps({"before": before_policy, "after": after_policy}),
            )
        )
    await db.flush()
    return workspace


async def list_members(db: AsyncSession, workspace_id: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.user_id == Membership.user_id)
            .where(Membership.workspace_id == workspace_id)
            .order_by(Membership.role.asc(), User.display_name.asc())
        )
    ).all()
    out: list[dict[str, Any]] = []
    for membership, user in rows:
        out.append(
            {
                "user_id": user.user_id,
                "email": user.email,
                "display_name": user.display_name,
                "role": membership.role,
                "membership_id": membership.membership_id,
            }
        )
    return out


async def count_owners(db: AsyncSession, workspace_id: str) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.role == "owner",
        )
    )
    return int(result.scalar_one())


async def update_member_role(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str,
    new_role: str,
    actor_role: str,
) -> Membership:
    """Change a member's role.

    Granting ``owner``, or demoting an existing owner, requires the caller to
    already be an owner (#62). Admins may only manage ``admin`` / ``member``.
    """
    if new_role not in _MEMBER_ROLES:
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, f"Invalid role '{new_role}'")
    if actor_role not in _ADMIN_ROLES:
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN, "Workspace admin or owner required"
        )
    row = await get_membership(db, workspace_id=workspace_id, user_id=user_id)
    if row is None:
        raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Membership not found")
    owner_change = new_role == "owner" or row.role == "owner"
    if owner_change and actor_role != "owner":
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN,
            "Only a workspace owner can grant, demote, or change owner roles",
        )
    if (
        row.role == "owner"
        and new_role != "owner"
        and await count_owners(db, workspace_id) <= 1
    ):
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN, "Cannot demote the last owner"
        )
    row.role = new_role
    await db.flush()
    return row


async def remove_member(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str,
    actor_role: str,
) -> None:
    """Remove a member. Removing an owner requires the caller to be an owner (#62)."""
    if actor_role not in _ADMIN_ROLES:
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN, "Workspace admin or owner required"
        )
    row = await get_membership(db, workspace_id=workspace_id, user_id=user_id)
    if row is None:
        raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Membership not found")
    if row.role == "owner" and actor_role != "owner":
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN, "Only a workspace owner can remove an owner"
        )
    if row.role == "owner" and await count_owners(db, workspace_id) <= 1:
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN, "Cannot remove the last owner"
        )
    await db.delete(row)
    await db.flush()


async def create_invite(
    db: AsyncSession,
    *,
    workspace_id: str,
    email: str,
    role: str,
    invited_by: str,
    ttl: timedelta = timedelta(days=7),
) -> tuple[WorkspaceInvite, str]:
    if role not in _INVITE_ROLES:
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, f"Invalid invite role '{role}'")
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, "Valid email required")

    ws = await db.get(Workspace, workspace_id)
    if ws is not None:
        try:
            await check_member_quota(db, ws)
        except QuotaExceeded as exc:
            raise WorkspaceServiceError(
                ErrorCode.QUOTA_EXCEEDED,
                f"Workspace quota '{exc.quota}' exceeded",
                details={"quota": exc.quota, "limit": exc.limit, "current": exc.current},
            ) from exc

    # Revoke prior pending invites for same email.
    pending = (
        await db.execute(
            select(WorkspaceInvite).where(
                WorkspaceInvite.workspace_id == workspace_id,
                WorkspaceInvite.email == normalized,
                WorkspaceInvite.accepted_at.is_(None),
                WorkspaceInvite.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    now = _now()
    for old in pending:
        old.revoked_at = now
    plaintext = secrets.token_urlsafe(32)
    invite = WorkspaceInvite(
        invite_id=f"inv_{uuid.uuid4().hex[:16]}",
        workspace_id=workspace_id,
        email=normalized,
        role=role,
        token_hash=_hash_token(plaintext),
        invited_by=invited_by,
        expires_at=now + ttl,
        accepted_at=None,
        revoked_at=None,
    )
    db.add(invite)
    await db.flush()
    return invite, plaintext


async def accept_invite(
    db: AsyncSession, *, token: str, user: User
) -> Membership:
    token_hash = _hash_token(token)
    invite = (
        await db.execute(
            select(WorkspaceInvite).where(WorkspaceInvite.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if invite is None:
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, "Invalid invite token")
    if invite.revoked_at is not None:
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, "Invite revoked")
    if invite.accepted_at is not None:
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, "Invite already used")
    if invite.expires_at <= _now():
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, "Invite expired")

    user_email = (user.email or "").strip().lower()
    if user_email != invite.email:
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN, "Invite email does not match signed-in user"
        )
    # Local seeded owner may accept without IdP; everyone else needs a verified identity (#62).
    identities = (
        await db.execute(select(UserIdentity).where(UserIdentity.user_id == user.user_id))
    ).scalars().all()
    has_verified = any(i.email_verified for i in identities)
    if user.user_id == LOCAL_USER_ID:
        if identities and not has_verified:
            raise WorkspaceServiceError(
                ErrorCode.ROLE_FORBIDDEN,
                "email_verified identity required to accept invite",
            )
    elif not has_verified:
        raise WorkspaceServiceError(
            ErrorCode.ROLE_FORBIDDEN,
            "email_verified identity required to accept invite",
        )

    existing = await get_membership(
        db, workspace_id=invite.workspace_id, user_id=user.user_id
    )
    if existing is not None:
        invite.accepted_at = _now()
        await db.flush()
        return existing

    ws = await db.get(Workspace, invite.workspace_id)
    if ws is not None:
        try:
            await check_member_quota(db, ws)
        except QuotaExceeded as exc:
            raise WorkspaceServiceError(
                ErrorCode.QUOTA_EXCEEDED,
                f"Workspace quota '{exc.quota}' exceeded",
                details={"quota": exc.quota, "limit": exc.limit, "current": exc.current},
            ) from exc

    await ensure_user_actor(db, user)
    membership = Membership(
        membership_id=f"mem_{invite.workspace_id}_{user.user_id}",
        workspace_id=invite.workspace_id,
        user_id=user.user_id,
        role=invite.role,
    )
    db.add(membership)
    invite.accepted_at = _now()
    await db.flush()
    return membership
