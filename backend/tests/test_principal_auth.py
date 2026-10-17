"""Issue #23 slice: Principal access tokens + local tenancy seed."""

from __future__ import annotations

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token, verify_access_token
from ak5.server.models.workspace import (
    LOCAL_WORKSPACE_ID,
    Agent,
    Membership,
    User,
    Workspace,
)
from ak5.server.seed import (
    LOCAL_AGENT_REVIEWER_ID,
    LOCAL_AGENT_WORKER_ID,
    LOCAL_USER_ID,
    ensure_local_tenancy,
)
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ensure_local_tenancy_idempotent(test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    assert await test_db.get(Workspace, LOCAL_WORKSPACE_ID) is not None
    assert await test_db.get(User, LOCAL_USER_ID) is not None
    assert await test_db.get(Agent, LOCAL_AGENT_WORKER_ID) is not None
    assert await test_db.get(Agent, LOCAL_AGENT_REVIEWER_ID) is not None
    mem = (
        await test_db.get(Membership, f"mem_{LOCAL_WORKSPACE_ID}_{LOCAL_USER_ID}")
    )
    assert mem is not None
    assert mem.role == "owner"


@pytest.mark.asyncio
async def test_user_token_verifies_as_human(test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    principal = Principal(
        principal_id=LOCAL_USER_ID,
        principal_type="user",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.HUMAN,
        membership_role="owner",
    )
    token = issue_access_token(principal)
    loaded = await verify_access_token(token, test_db)
    assert loaded.principal_type == "user"
    assert loaded.workflow_role == Role.HUMAN
    assert loaded.workspace_id == LOCAL_WORKSPACE_ID


@pytest.mark.asyncio
async def test_agent_token_rejects_role_mismatch(test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    bad = Principal(
        principal_id=LOCAL_AGENT_WORKER_ID,
        principal_type="agent",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.GATEKEEPER,  # DB has worker
    )
    token = issue_access_token(bad)
    with pytest.raises(Exception) as exc:
        await verify_access_token(token, test_db)
    assert "401" in str(exc.value.status_code) or exc.value.status_code == 401


@pytest.mark.asyncio
async def test_agent_token_matches_workflow_role(test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    principal = Principal(
        principal_id=LOCAL_AGENT_WORKER_ID,
        principal_type="agent",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.WORKER,
    )
    token = issue_access_token(principal)
    loaded = await verify_access_token(token, test_db)
    assert loaded.principal_type == "agent"
    assert loaded.workflow_role == Role.WORKER
