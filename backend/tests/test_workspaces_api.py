"""Issue #34: workspace CRUD, membership, and email invites."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.models.actor import Actor
from ak5.server.models.board import Board
from ak5.server.models.column import Column
from ak5.server.models.workspace import (
    LOCAL_WORKSPACE_ID,
    Membership,
    User,
    UserIdentity,
    Workspace,
)
from ak5.server.models.workspace_invite import WorkspaceInvite
from ak5.server.seed import LOCAL_AGENT_WORKER_ID, LOCAL_USER_ID, ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _user_headers(
    user_id: str = LOCAL_USER_ID,
    *,
    workspace_id: str = LOCAL_WORKSPACE_ID,
    membership_role: str = "owner",
) -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=user_id,
            principal_type="user",
            workspace_id=workspace_id,
            workflow_role=Role.HUMAN,
            membership_role=membership_role,
        )
    )
    return {"Authorization": f"Bearer {token}"}


def _agent_headers() -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=LOCAL_AGENT_WORKER_ID,
            principal_type="agent",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=Role.WORKER,
        )
    )
    return {"Authorization": f"Bearer {token}"}


async def _ensure_user_actor(db: AsyncSession, user_id: str, *, name: str) -> None:
    if await db.get(Actor, user_id) is None:
        db.add(
            Actor(
                actor_id=user_id,
                actor_type="human",
                name=name,
                role="human",
                capabilities="[]",
                status="idle",
            )
        )


@pytest.mark.asyncio
async def test_create_workspace_provisions_inbox(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    resp = await client.post(
        "/api/v1/workspaces",
        headers=_user_headers(),
        json={"name": "Acme"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Acme"
    assert body["membership_role"] == "owner"
    assert body["inbox_board_id"] == f"{body['workspace_id']}__inbox"
    assert body["policy"]["max_auto_boards"] == 50

    board = await test_db.get(Board, body["inbox_board_id"])
    assert board is not None
    assert board.kind == "inbox"
    assert board.workspace_id == body["workspace_id"]

    cols = (
        await test_db.execute(
            select(Column).where(Column.board_id == body["inbox_board_id"])
        )
    ).scalars().all()
    assert cols, "inbox must provision at least one column"
    assert all(c.workspace_id == body["workspace_id"] for c in cols)


@pytest.mark.asyncio
async def test_agent_cannot_manage_workspaces(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    resp = await client.get("/api/v1/workspaces", headers=_agent_headers())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ROLE_FORBIDDEN"


@pytest.mark.asyncio
async def test_member_cannot_invite_or_patch_policy(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    member_id = "usr_member_only"
    test_db.add(User(user_id=member_id, email="m@example.com", display_name="Member"))
    await _ensure_user_actor(test_db, member_id, name="Member")
    test_db.add(
        Membership(
            membership_id=f"mem_{LOCAL_WORKSPACE_ID}_{member_id}",
            workspace_id=LOCAL_WORKSPACE_ID,
            user_id=member_id,
            role="member",
        )
    )
    await test_db.commit()

    headers = _user_headers(member_id, membership_role="member")
    invite = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members",
        headers=headers,
        json={"email": "new@example.com", "role": "member"},
    )
    assert invite.status_code == 403
    assert invite.json()["error"]["code"] == "ROLE_FORBIDDEN"

    patch = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}",
        headers=headers,
        json={"policy": {"max_auto_boards": 10}},
    )
    assert patch.status_code == 403


@pytest.mark.asyncio
async def test_invite_accept_expire_and_reuse(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    # Invitee already belongs to a home workspace so their access token verifies.
    home_ws = "ws_home"
    test_db.add(Workspace(workspace_id=home_ws, name="Home", policy="{}"))
    invitee_id = "usr_invitee"
    test_db.add(
        User(user_id=invitee_id, email="invitee@example.com", display_name="Invitee")
    )
    await _ensure_user_actor(test_db, invitee_id, name="Invitee")
    test_db.add(
        Membership(
            membership_id=f"mem_{home_ws}_{invitee_id}",
            workspace_id=home_ws,
            user_id=invitee_id,
            role="owner",
        )
    )
    test_db.add(
        UserIdentity(
            identity_id="id_invitee",
            user_id=invitee_id,
            issuer="https://idp.example",
            subject="sub-invitee",
            email_verified=True,
        )
    )
    await test_db.commit()

    created = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members",
        headers=_user_headers(),
        json={"email": "invitee@example.com", "role": "member"},
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    assert token

    invitee_headers = _user_headers(
        invitee_id, workspace_id=home_ws, membership_role="owner"
    )
    accepted = await client.post(
        "/api/v1/workspaces/invites/accept",
        headers=invitee_headers,
        json={"token": token},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "member"

    reuse = await client.post(
        "/api/v1/workspaces/invites/accept",
        headers=invitee_headers,
        json={"token": token},
    )
    assert reuse.status_code == 422
    assert reuse.json()["error"]["code"] == "PAYLOAD_INVALID"

    # Fresh invite then expire it in DB
    created2 = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members",
        headers=_user_headers(),
        json={"email": "other@example.com", "role": "admin"},
    )
    assert created2.status_code == 200
    token2 = created2.json()["token"]
    invite_id = created2.json()["invite_id"]
    row = await test_db.get(WorkspaceInvite, invite_id)
    assert row is not None
    row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    await test_db.commit()

    other = "usr_other_invitee"
    test_db.add(User(user_id=other, email="other@example.com", display_name="Other"))
    await _ensure_user_actor(test_db, other, name="Other")
    test_db.add(
        Membership(
            membership_id=f"mem_{home_ws}_{other}",
            workspace_id=home_ws,
            user_id=other,
            role="member",
        )
    )
    await test_db.commit()
    expired = await client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_user_headers(other, workspace_id=home_ws, membership_role="member"),
        json={"token": token2},
    )
    assert expired.status_code == 422
    assert "expired" in expired.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_last_owner_protected(client: AsyncClient, test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    demote = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{LOCAL_USER_ID}",
        headers=_user_headers(),
        json={"role": "admin"},
    )
    assert demote.status_code == 403
    assert demote.json()["error"]["code"] == "ROLE_FORBIDDEN"

    delete = await client.delete(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{LOCAL_USER_ID}",
        headers=_user_headers(),
    )
    assert delete.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_grant_or_remove_owner(
    client: AsyncClient, test_db: AsyncSession
):
    """#62: non-owner admin must not escalate to owner or demote/remove owners."""
    await ensure_local_tenancy(test_db)
    admin_id = "usr_ws_admin"
    member_id = "usr_ws_member"
    test_db.add(User(user_id=admin_id, email="admin@example.com", display_name="Admin"))
    test_db.add(User(user_id=member_id, email="mem@example.com", display_name="Mem"))
    await _ensure_user_actor(test_db, admin_id, name="Admin")
    await _ensure_user_actor(test_db, member_id, name="Mem")
    test_db.add(
        Membership(
            membership_id=f"mem_{LOCAL_WORKSPACE_ID}_{admin_id}",
            workspace_id=LOCAL_WORKSPACE_ID,
            user_id=admin_id,
            role="admin",
        )
    )
    test_db.add(
        Membership(
            membership_id=f"mem_{LOCAL_WORKSPACE_ID}_{member_id}",
            workspace_id=LOCAL_WORKSPACE_ID,
            user_id=member_id,
            role="member",
        )
    )
    await test_db.commit()

    admin_headers = _user_headers(admin_id, membership_role="admin")

    self_owner = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{admin_id}",
        headers=admin_headers,
        json={"role": "owner"},
    )
    assert self_owner.status_code == 403, self_owner.text
    assert self_owner.json()["error"]["code"] == "ROLE_FORBIDDEN"

    promote_other = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{member_id}",
        headers=admin_headers,
        json={"role": "owner"},
    )
    assert promote_other.status_code == 403
    assert promote_other.json()["error"]["code"] == "ROLE_FORBIDDEN"

    demote_owner = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{LOCAL_USER_ID}",
        headers=admin_headers,
        json={"role": "admin"},
    )
    assert demote_owner.status_code == 403
    assert demote_owner.json()["error"]["code"] == "ROLE_FORBIDDEN"

    delete_owner = await client.delete(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{LOCAL_USER_ID}",
        headers=admin_headers,
    )
    assert delete_owner.status_code == 403
    assert delete_owner.json()["error"]["code"] == "ROLE_FORBIDDEN"

    # Admin may still manage non-owner members.
    promote_admin = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{member_id}",
        headers=admin_headers,
        json={"role": "admin"},
    )
    assert promote_admin.status_code == 200, promote_admin.text
    assert promote_admin.json()["role"] == "admin"

    # Owner can grant owner and demote a non-last owner after a second owner exists.
    grant = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{admin_id}",
        headers=_user_headers(),
        json={"role": "owner"},
    )
    assert grant.status_code == 200, grant.text
    assert grant.json()["role"] == "owner"

    demote_ok = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members/{admin_id}",
        headers=_user_headers(),
        json={"role": "admin"},
    )
    assert demote_ok.status_code == 200, demote_ok.text
    assert demote_ok.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_accept_invite_requires_verified_identity(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    home_ws = "ws_home_unver"
    test_db.add(Workspace(workspace_id=home_ws, name="Home", policy="{}"))
    invitee_id = "usr_unver"
    test_db.add(
        User(user_id=invitee_id, email="unver@example.com", display_name="Unver")
    )
    await _ensure_user_actor(test_db, invitee_id, name="Unver")
    test_db.add(
        Membership(
            membership_id=f"mem_{home_ws}_{invitee_id}",
            workspace_id=home_ws,
            user_id=invitee_id,
            role="owner",
        )
    )
    await test_db.commit()

    created = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/members",
        headers=_user_headers(),
        json={"email": "unver@example.com", "role": "member"},
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]

    denied = await client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_user_headers(
            invitee_id, workspace_id=home_ws, membership_role="owner"
        ),
        json={"token": token},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "ROLE_FORBIDDEN"


@pytest.mark.asyncio
async def test_policy_patch_writes_audit(
    client: AsyncClient, test_db: AsyncSession
):
    from ak5.server.models.audit import AuditLog

    await ensure_local_tenancy(test_db)
    await test_db.commit()

    resp = await client.patch(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}",
        headers=_user_headers(),
        json={
            "name": "Local Renamed",
            "policy": {
                "max_auto_boards": 12,
                "allowed_email_domains": ["acme.com"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Local Renamed"
    assert resp.json()["policy"]["max_auto_boards"] == 12
    assert resp.json()["policy"]["allowed_email_domains"] == ["acme.com"]

    logs = (
        await test_db.execute(
            select(AuditLog).where(AuditLog.action == "WORKSPACE_POLICY_UPDATED")
        )
    ).scalars().all()
    assert len(logs) >= 1
    assert logs[-1].workspace_id == LOCAL_WORKSPACE_ID
