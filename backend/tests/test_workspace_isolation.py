"""Issue #23: workspace isolation on boards API."""

from __future__ import annotations

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.models.board import Board
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID, Membership, User, Workspace
from ak5.server.seed import LOCAL_USER_ID, ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_list_boards_hides_other_workspace(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    other_ws = "ws_other"
    if await test_db.get(Workspace, other_ws) is None:
        test_db.add(Workspace(workspace_id=other_ws, name="Other", policy="{}"))
    other_user = "usr_other"
    if await test_db.get(User, other_user) is None:
        test_db.add(User(user_id=other_user, email="o@x", display_name="Other"))
    mem_id = f"mem_{other_ws}_{other_user}"
    if await test_db.get(Membership, mem_id) is None:
        test_db.add(
            Membership(
                membership_id=mem_id,
                workspace_id=other_ws,
                user_id=other_user,
                role="owner",
            )
        )
    test_db.add(
        Board(
            board_id="board-other-ws",
            workspace_id=other_ws,
            name="Secret Board",
            created_by="user_pm",
            kind="standard",
            template="default",
        )
    )
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
