"""Issue #27: workspace agent create / credential / disable APIs."""

from __future__ import annotations

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token, verify_access_token
from ak5.server.models.actor import Actor
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID, Membership, User
from ak5.server.seed import LOCAL_USER_ID, ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


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


def _headers(
    user_id: str = LOCAL_USER_ID,
    *,
    role: str = "owner",
) -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=user_id,
            principal_type="user",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=Role.HUMAN,
            membership_role=role,
        )
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_creates_agent_and_member_lists(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    created = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/agents",
        headers=_headers(),
        json={
            "name": "UI Worker",
            "workflow_role": "worker",
            "capabilities": ["python"],
            "board_scope": [],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "UI Worker"
    assert body["workflow_role"] == "worker"
    assert body["capabilities"] == ["python"]
    assert len(body["credentials"]) == 1
    plaintext = body["credentials"][0]["access_token"]
    assert plaintext and plaintext.startswith("ak5_agt_")

    principal = await verify_access_token(plaintext, test_db)
    assert principal.principal_type == "agent"
    assert principal.principal_id == body["agent_id"]

    listed = await client.get(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/agents",
        headers=_headers(),
    )
    assert listed.status_code == 200
    row = next(a for a in listed.json() if a["agent_id"] == body["agent_id"])
    assert row["credentials"][0].get("access_token") in (None, "")
    assert row["credentials"][0]["last_used_at"] is not None  # touched by verify


@pytest.mark.asyncio
async def test_member_cannot_create_agent(client: AsyncClient, test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    member_id = "usr_agent_member"
    test_db.add(User(user_id=member_id, email="m@ex.com", display_name="M"))
    await _ensure_user_actor(test_db, member_id, name="M")
    test_db.add(
        Membership(
            membership_id=f"mem_{LOCAL_WORKSPACE_ID}_{member_id}",
            workspace_id=LOCAL_WORKSPACE_ID,
            user_id=member_id,
            role="member",
        )
    )
    await test_db.commit()

    denied = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/agents",
        headers=_headers(member_id, role="member"),
        json={"name": "Nope", "workflow_role": "worker"},
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_disable_and_revoke_credential(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    created = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/agents",
        headers=_headers(),
        json={"name": "Temp", "workflow_role": "triage"},
    )
    assert created.status_code == 200
    agent_id = created.json()["agent_id"]
    cred_id = created.json()["credentials"][0]["credential_id"]

    revoked = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/agents/{agent_id}"
        f"/credentials/{cred_id}/revoke",
        headers=_headers(),
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    disabled = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/agents/{agent_id}/disable",
        headers=_headers(),
    )
    assert disabled.status_code == 200
    assert disabled.json()["disabled_at"] is not None

    reissue = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/agents/{agent_id}/credentials",
        headers=_headers(),
        json={},
    )
    assert reissue.status_code == 403
