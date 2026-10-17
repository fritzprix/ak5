import json

import pytest
from ak5.models.actor import Actor
from ak5.models.column import Column
from ak5.models.ticket import Ticket
from ak5.services.ticket_status import derive_status
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload


@pytest.mark.asyncio
async def test_list_boards(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    resp = await client.get("/api/v1/boards")
    assert resp.status_code == 200
    boards = resp.json()
    assert isinstance(boards, list)
    assert len(boards) >= 1
    assert boards[0]["board_id"] == "proj-core-engine"
    assert "name" in boards[0]
    assert "columns" not in boards[0]

    create_resp = await client.post(
        "/api/v1/boards",
        json={"board_id": "board-beta", "name": "Beta Board", "description": "Second board"},
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text

    resp2 = await client.get("/api/v1/boards")
    assert resp2.status_code == 200
    ids = {b["board_id"] for b in resp2.json()}
    assert "proj-core-engine" in ids
    assert "board-beta" in ids


@pytest.mark.asyncio
async def test_wip_limit_blocks_claim(client: AsyncClient, auth_headers, test_db):
    headers = auth_headers("user_pm", "human")
    for i in range(4):
        test_db.add(
            Actor(
                actor_id=f"agent_bwip_{i}",
                actor_type="agent",
                name=f"BW {i}",
                role="worker",
                capabilities=json.dumps([]),
                status="idle",
            )
        )
    await test_db.commit()

    ticket_ids: list[str] = []
    for i in range(3):
        create = await client.post(
            "/api/v1/tickets",
            json=board_ticket(f"WIP filler {i}"),
            headers=headers,
        )
        assert create.status_code == 201, create.text
        ticket_ids.append(create.json()["ticket_id"])
        claim = await client.post(
            f"/api/v1/tickets/{ticket_ids[-1]}/claim",
            headers=auth_headers(f"agent_bwip_{i}", "agent"),
        )
        assert claim.status_code == 200, claim.text

    todo = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Waiting to enter"),
        headers=headers,
    )
    assert todo.status_code == 201
    waiting_id = todo.json()["ticket_id"]

    # Stage jump via move is forbidden (409 INVALID_TRANSITION)
    move = await client.patch(
        f"/api/v1/tickets/{waiting_id}/move",
        json={"target_column_id": "col_in_progress"},
        headers=headers,
    )
    assert move.status_code == 409

    # Claim hits WIP
    claim = await client.post(
        f"/api/v1/tickets/{waiting_id}/claim",
        headers=auth_headers("agent_bwip_3", "agent"),
    )
    assert claim.status_code == 409
    assert claim.json()["error"]["code"] == "WIP_LIMIT"

    # Reorder within a full column is still allowed
    reorder = await client.patch(
        f"/api/v1/tickets/{ticket_ids[0]}/move",
        json={
            "target_column_id": "col_in_progress",
            "previous_ticket_id": ticket_ids[1],
            "next_ticket_id": ticket_ids[2] if len(ticket_ids) > 2 else None,
        },
        headers=headers,
    )
    assert reorder.status_code == 200


@pytest.mark.asyncio
async def test_board_done_limit_and_archive_filter(client: AsyncClient, auth_headers, test_db):
    headers = auth_headers("user_pm", "human")

    created_ids = []
    for i in range(3):
        res = await client.post(
            "/api/v1/tickets",
            json=board_ticket(f"Done Task {i}"),
            headers=headers,
        )
        assert res.status_code == 201
        created_ids.append(res.json()["ticket_id"])

    # Place on done via DB for board listing tests
    done_col = await test_db.get(Column, "col_done")
    assert done_col is not None
    for tid in created_ids:
        ticket = (
            await test_db.execute(
                select(Ticket).where(Ticket.ticket_id == tid).options(selectinload(Ticket.column))
            )
        ).scalar_one()
        ticket.column_id = done_col.column_id
        ticket.column = done_col
        ticket.status = derive_status(ticket)
    await test_db.commit()

    arc_res = await client.post(f"/api/v1/tickets/{created_ids[0]}/archive", headers=headers)
    assert arc_res.status_code == 200

    board_res = await client.get("/api/v1/boards/proj-core-engine")
    assert board_res.status_code == 200
    done_col_out = next(c for c in board_res.json()["columns"] if c["column_id"] == "col_done")
    done_ticket_ids = [t["ticket_id"] for t in done_col_out["tickets"]]
    assert created_ids[0] not in done_ticket_ids
    assert created_ids[1] in done_ticket_ids
    assert created_ids[2] in done_ticket_ids

    board_arc_res = await client.get("/api/v1/boards/proj-core-engine?include_archived=true")
    assert board_arc_res.status_code == 200
    done_col_arc = next(c for c in board_arc_res.json()["columns"] if c["column_id"] == "col_done")
    done_arc_ids = [t["ticket_id"] for t in done_col_arc["tickets"]]
    assert created_ids[0] in done_arc_ids

    limit_res = await client.get("/api/v1/boards/proj-core-engine?done_limit=1")
    assert limit_res.status_code == 200
    done_col_limit = next(c for c in limit_res.json()["columns"] if c["column_id"] == "col_done")
    assert len(done_col_limit["tickets"]) == 1
    assert done_col_limit["total_ticket_count"] >= 2
