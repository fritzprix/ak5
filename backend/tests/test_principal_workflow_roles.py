"""Principal-first workflow roles + §9 FK rewrite (issue #23)."""

from __future__ import annotations

import pytest
from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.server.auth.bridge import ACTOR_TO_PRINCIPAL
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.authz import require_roles, workflow_role_of
from ak5.server.models.actor import Actor
from ak5.server.models.board import Board
from ak5.server.models.ticket import Ticket
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
from ak5.server.routers.auth import resolve_auth_context
from ak5.server.seed import (
    LOCAL_AGENT_WORKER_ID,
    LOCAL_USER_ID,
    ensure_local_tenancy,
    ensure_principal_actor_aliases,
    migrate_v1_actors_to_principals,
    rewrite_actor_refs_to_principals,
)
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _principal_headers(principal: Principal) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_access_token(principal)}"}


@pytest.mark.asyncio
async def test_workflow_role_of_uses_attached_principal_not_actor_columns(
    test_db: AsyncSession,
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    # Legacy actor row with misleading role; Principal says HUMAN.
    actor = await test_db.get(Actor, LOCAL_USER_ID) or await test_db.get(Actor, "user_pm")
    assert actor is not None
    token = issue_access_token(
        Principal(
            principal_id=LOCAL_USER_ID,
            principal_type="user",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=Role.HUMAN,
            membership_role="owner",
        )
    )
    ctx = await resolve_auth_context(token, test_db)
    assert workflow_role_of(ctx.actor) == Role.HUMAN
    # Even if Actor columns were patched under us, attached Principal wins.
    ctx.actor.role = "worker"
    ctx.actor.actor_type = "agent"
    assert workflow_role_of(ctx.actor) == Role.HUMAN


@pytest.mark.asyncio
async def test_agent_principal_cannot_reopen_done_ticket(
    client: AsyncClient, test_db: AsyncSession, auth_headers
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    human = auth_headers("user_pm", "human")
    worker = auth_headers("agent_image_worker", "agent")
    # Seed agent_image_worker role is "Media Specialist" — use Principal JWT for worker role.
    worker_principal = _principal_headers(
        Principal(
            principal_id=LOCAL_AGENT_WORKER_ID,
            principal_type="agent",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=Role.WORKER,
        )
    )

    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Done then reopen"),
        headers=human,
    )
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]

    # Force done stage for reopen gate (role check is first).
    ticket = await test_db.get(Ticket, tid)
    assert ticket is not None
    # status is derived from the column stage (#35): move to the done column.
    from ak5.server.models.column import Column
    from sqlalchemy import select

    done_col = (
        await test_db.execute(
            select(Column).where(
                Column.board_id == "proj-core-engine",
                Column.stage == "done",
            )
        )
    ).scalar_one()
    ticket.column_id = done_col.column_id
    ticket.column = done_col
    await test_db.commit()

    bad = await client.post(
        f"/api/v1/tickets/{tid}/reopen",
        json={"reason": "agent should not reopen"},
        headers=worker_principal,
    )
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == ErrorCode.ROLE_FORBIDDEN.value

    # Sanity: legacy worker actor JWT also forbidden (non-human role).
    bad2 = await client.post(
        f"/api/v1/tickets/{tid}/reopen",
        json={"reason": "legacy agent"},
        headers=worker,
    )
    assert bad2.status_code == 403


@pytest.mark.asyncio
async def test_user_principal_cannot_claim_backlog(
    client: AsyncClient, test_db: AsyncSession, auth_headers
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    human_legacy = auth_headers("user_pm", "human")
    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket("No human claim"),
        headers=human_legacy,
    )
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]

    user_principal = _principal_headers(
        Principal(
            principal_id=LOCAL_USER_ID,
            principal_type="user",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=Role.HUMAN,
            membership_role="owner",
        )
    )
    bad = await client.post(f"/api/v1/tickets/{tid}/claim", headers=user_principal)
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == ErrorCode.ROLE_FORBIDDEN.value


@pytest.mark.asyncio
async def test_require_roles_accepts_principal_directly():
    human = Principal(
        principal_id=LOCAL_USER_ID,
        principal_type="user",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.HUMAN,
        membership_role="owner",
    )
    assert require_roles(human, Role.HUMAN) == Role.HUMAN
    with pytest.raises(Exception) as exc:
        require_roles(human, Role.WORKER)
    assert exc.value.code == ErrorCode.ROLE_FORBIDDEN

    worker = Principal(
        principal_id=LOCAL_AGENT_WORKER_ID,
        principal_type="agent",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.WORKER,
    )
    assert require_roles(worker, Role.WORKER) == Role.WORKER
    with pytest.raises(Exception) as exc2:
        require_roles(worker, Role.HUMAN)
    assert exc2.value.code == ErrorCode.ROLE_FORBIDDEN


@pytest.mark.asyncio
async def test_migrate_rewrites_ticket_and_board_refs_to_principal_ids(
    test_db: AsyncSession,
):
    # conftest already seeded user_pm boards; migrate should rewrite created_by.
    board = await test_db.get(Board, "proj-core-engine")
    assert board is not None
    assert board.created_by == "user_pm"

    ticket = Ticket(
        ticket_id="AK-REWRITE-1",
        workspace_id=LOCAL_WORKSPACE_ID,
        board_id="proj-core-engine",
        column_id="col_todo",
        title="Rewrite me",
        priority="medium",
        rank="a",
        labels="[]",
        created_by="user_pm",
        claimed_by="agent_image_worker",
    )
    test_db.add(ticket)
    await test_db.commit()

    await ensure_local_tenancy(test_db)
    await migrate_v1_actors_to_principals(test_db)
    await test_db.commit()

    await test_db.refresh(board)
    await test_db.refresh(ticket)
    assert board.created_by == LOCAL_USER_ID
    assert ticket.created_by == LOCAL_USER_ID
    assert ticket.claimed_by == LOCAL_AGENT_WORKER_ID
    assert "user_pm" in ACTOR_TO_PRINCIPAL
    assert await test_db.get(Actor, LOCAL_USER_ID) is not None

    # Idempotent
    await ensure_principal_actor_aliases(test_db)
    await rewrite_actor_refs_to_principals(test_db)
    await test_db.commit()
    await test_db.refresh(ticket)
    assert ticket.created_by == LOCAL_USER_ID
