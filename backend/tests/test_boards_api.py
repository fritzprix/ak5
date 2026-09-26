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


@pytest.mark.asyncio
async def test_board_done_limit_and_archive_filter(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # Create 3 tickets directly in col_done
    created_ids = []
    for i in range(3):
        res = await client.post(
            "/api/v1/tickets",
            json={
                "title": f"Done Task {i}",
                "board_id": "proj-core-engine",
                "column_id": "col_done",
            },
            headers=headers,
        )
        assert res.status_code == 201
        created_ids.append(res.json()["ticket_id"])

    # Archive the first ticket
    arc_res = await client.post(f"/api/v1/tickets/{created_ids[0]}/archive", headers=headers)
    assert arc_res.status_code == 200

    # 1. Fetch board with include_archived=False (default)
    board_res = await client.get("/api/v1/boards/proj-core-engine")
    assert board_res.status_code == 200
    done_col = next(c for c in board_res.json()["columns"] if c["column_id"] == "col_done")
    # created_ids[0] should be excluded
    done_ticket_ids = [t["ticket_id"] for t in done_col["tickets"]]
    assert created_ids[0] not in done_ticket_ids
    assert created_ids[1] in done_ticket_ids
    assert created_ids[2] in done_ticket_ids

    # 2. Fetch board with include_archived=True
    board_arc_res = await client.get("/api/v1/boards/proj-core-engine?include_archived=true")
    assert board_arc_res.status_code == 200
    done_col_arc = next(c for c in board_arc_res.json()["columns"] if c["column_id"] == "col_done")
    done_arc_ids = [t["ticket_id"] for t in done_col_arc["tickets"]]
    assert created_ids[0] in done_arc_ids

    # 3. Test done_limit parameter
    limit_res = await client.get("/api/v1/boards/proj-core-engine?done_limit=1")
    assert limit_res.status_code == 200
    done_col_limit = next(c for c in limit_res.json()["columns"] if c["column_id"] == "col_done")
    assert len(done_col_limit["tickets"]) == 1
    assert done_col_limit["total_ticket_count"] >= 2
