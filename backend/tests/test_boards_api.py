import pytest
from httpx import AsyncClient


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

    # Create a second board and ensure it appears in the list
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
async def test_wip_limit_blocks_move(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # Fill in_progress to WIP limit (3)
    ticket_ids: list[str] = []
    for i in range(3):
        create = await client.post(
            "/api/v1/tickets",
            json={
                "title": f"WIP filler {i}",
                "board_id": "proj-core-engine",
                "column_id": "col_in_progress",
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        ticket_ids.append(create.json()["ticket_id"])

    # Fourth create into same column must fail
    over_create = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Over WIP",
            "board_id": "proj-core-engine",
            "column_id": "col_in_progress",
        },
        headers=headers,
    )
    assert over_create.status_code == 409

    # Create in todo, then move into full in_progress — must fail
    todo = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Waiting to enter",
            "board_id": "proj-core-engine",
            "column_id": "col_todo",
        },
        headers=headers,
    )
    assert todo.status_code == 201
    waiting_id = todo.json()["ticket_id"]

    move = await client.patch(
        f"/api/v1/tickets/{waiting_id}/move",
        json={"target_column_id": "col_in_progress"},
        headers=headers,
    )
    assert move.status_code == 409

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
