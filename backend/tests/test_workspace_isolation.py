"""Issue #23: workspace isolation on boards / tickets API."""

from __future__ import annotations

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.models.board import Board
from ak5.server.models.column import Column
from ak5.server.models.ticket import Ticket
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID, Membership, User, Workspace
from ak5.server.seed import LOCAL_USER_ID, ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _ensure_other_workspace(db: AsyncSession, *, board_id: str = "board-other-ws") -> str:
    other_ws = "ws_other"
    if await db.get(Workspace, other_ws) is None:
        db.add(Workspace(workspace_id=other_ws, name="Other", policy="{}"))
    other_user = "usr_other"
    if await db.get(User, other_user) is None:
        db.add(User(user_id=other_user, email="o@x", display_name="Other"))
    mem_id = f"mem_{other_ws}_{other_user}"
    if await db.get(Membership, mem_id) is None:
        db.add(
            Membership(
                membership_id=mem_id,
                workspace_id=other_ws,
                user_id=other_user,
                role="owner",
            )
        )
    if await db.get(Board, board_id) is None:
        db.add(
            Board(
                board_id=board_id,
                workspace_id=other_ws,
                name="Secret Board",
                created_by="user_pm",
                kind="standard",
                template="default",
            )
        )
    return other_ws


@pytest.mark.asyncio
async def test_list_boards_hides_other_workspace(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await _ensure_other_workspace(test_db)
    await test_db.commit()

    # Legacy actor JWT → scoped to ws_local
    listed = await client.get("/api/v1/boards", headers=auth_headers("user_pm", "human"))
    assert listed.status_code == 200
    ids = {b["board_id"] for b in listed.json()}
    assert "board-other-ws" not in ids

    # Principal JWT for local user also cannot see other workspace board
    token = issue_access_token(
        Principal(
            principal_id=LOCAL_USER_ID,
            principal_type="user",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=Role.HUMAN,
            membership_role="owner",
        )
    )
    forbidden = await client.get(
        "/api/v1/boards/board-other-ws",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"


@pytest.mark.asyncio
async def test_list_tickets_hides_other_workspace(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    other_ws = await _ensure_other_workspace(test_db)
    col_id = "col-other-ws-backlog"
    if await test_db.get(Column, col_id) is None:
        test_db.add(
            Column(
                column_id=col_id,
                board_id="board-other-ws",
                name="Backlog",
                stage="backlog",
                position=1,
            )
        )
    ticket_id = "tkt-other-ws"
    if await test_db.get(Ticket, ticket_id) is None:
        test_db.add(
            Ticket(
                ticket_id=ticket_id,
                workspace_id=other_ws,
                board_id="board-other-ws",
                column_id=col_id,
                title="Secret ticket",
                description="should be hidden",
                priority="medium",
                rank="a0",
                created_by="user_pm",
            )
        )
    await test_db.commit()

    listed = await client.get("/api/v1/tickets", headers=auth_headers("user_pm", "human"))
    assert listed.status_code == 200
    ids = {t["ticket_id"] for t in listed.json()}
    assert ticket_id not in ids

    forbidden = await client.get(
        f"/api/v1/tickets/{ticket_id}",
        headers=auth_headers("user_pm", "human"),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"


@pytest.mark.asyncio
async def test_create_ticket_rejects_cross_workspace_board(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await _ensure_other_workspace(test_db)
    await test_db.commit()

    resp = await client.post(
        "/api/v1/tickets",
        headers=auth_headers("user_pm", "human"),
        json={
            "board_id": "board-other-ws",
            "title": "Cross-ws create",
            "spec": "must fail",
            "acceptance_criteria": ["no"],
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"


@pytest.mark.asyncio
async def test_claim_and_comment_reject_cross_workspace_ticket(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    other_ws = await _ensure_other_workspace(test_db)
    col_id = "col-other-ws-backlog"
    if await test_db.get(Column, col_id) is None:
        test_db.add(
            Column(
                column_id=col_id,
                board_id="board-other-ws",
                name="Backlog",
                stage="backlog",
                position=1,
            )
        )
    ticket_id = "tkt-other-ws-write"
    if await test_db.get(Ticket, ticket_id) is None:
        test_db.add(
            Ticket(
                ticket_id=ticket_id,
                workspace_id=other_ws,
                board_id="board-other-ws",
                column_id=col_id,
                title="Secret write target",
                description="must not claim",
                priority="medium",
                rank="a0",
                created_by="user_pm",
            )
        )
    await test_db.commit()

    headers = auth_headers("user_pm", "human")
    claim = await client.post(f"/api/v1/tickets/{ticket_id}/claim", headers=headers)
    assert claim.status_code == 403
    assert claim.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"

    comment = await client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        headers=headers,
        json={"content": "nope"},
    )
    assert comment.status_code == 403
    assert comment.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"

    patch = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        headers=headers,
        json={"title": "hijack"},
    )
    assert patch.status_code == 403
    assert patch.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"


@pytest.mark.asyncio
async def test_board_next_columns_members_reject_cross_workspace(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await _ensure_other_workspace(test_db)
    # Give other board a backlog column so add_column isn't the only path.
    col_id = "col-other-ws-backlog"
    if await test_db.get(Column, col_id) is None:
        test_db.add(
            Column(
                column_id=col_id,
                board_id="board-other-ws",
                name="Backlog",
                stage="backlog",
                position=1,
            )
        )
    await test_db.commit()

    headers = auth_headers("user_pm", "human")
    nxt = await client.get("/api/v1/boards/board-other-ws/next", headers=headers)
    assert nxt.status_code == 403
    assert nxt.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"

    members = await client.get("/api/v1/boards/board-other-ws/members", headers=headers)
    assert members.status_code == 403
    assert members.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"

    col = await client.post(
        "/api/v1/boards/board-other-ws/columns",
        headers=headers,
        json={
            "column_id": "col-leak",
            "name": "Leak",
            "stage": "backlog",
            "position": 9,
            "wip_limit": 0,
        },
    )
    assert col.status_code == 403
    assert col.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"


@pytest.mark.asyncio
async def test_list_actors_scoped_to_workspace(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    listed = await client.get("/api/v1/actors", headers=auth_headers("user_pm", "human"))
    assert listed.status_code == 200
    ids = {a["actor_id"] for a in listed.json()}
    # Seed locals are visible; a foreign actor never enrolled should not appear.
    assert "user_pm" in ids


def test_scoped_fail_closed_for_tenant_model_without_workspace_id():
    from ak5.contracts.stages import Role
    from ak5.server.auth.principal import Principal
    from ak5.server.auth.scope import ScopeError, scoped
    from ak5.server.models.event_log_meta import EventLogMeta
    from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
    from sqlalchemy import select

    principal = Principal(
        principal_id="usr_x",
        principal_type="user",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.HUMAN,
        membership_role="owner",
    )
    # EventLogMeta is listed as tenant but has no workspace_id → fail closed.
    with pytest.raises(ScopeError):
        scoped(select(EventLogMeta), principal, EventLogMeta)


def test_scoped_applies_workspace_filter_for_column():
    from ak5.contracts.stages import Role
    from ak5.server.auth.principal import Principal
    from ak5.server.auth.scope import scoped
    from ak5.server.models.column import Column
    from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
    from sqlalchemy import select

    principal = Principal(
        principal_id="usr_x",
        principal_type="user",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.HUMAN,
        membership_role="owner",
    )
    stmt = scoped(select(Column), principal, Column)
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "workspace_id" in compiled
    assert LOCAL_WORKSPACE_ID in compiled
