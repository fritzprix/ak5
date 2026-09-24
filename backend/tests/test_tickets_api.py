import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_get_ticket(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # 1. Create a ticket
    create_payload = {
        "title": "Build WebP Avatar Resizer",
        "description": "Resize user avatars to 200x200 webp",
        "board_id": "proj-core-engine",
        "column_id": "col_todo",
        "priority": "high",
        "labels": ["backend", "image"],
    }
    resp = await client.post("/api/v1/tickets", json=create_payload, headers=headers)
    assert resp.status_code == 201, resp.text
    ticket_data = resp.json()
    assert ticket_data["title"] == "Build WebP Avatar Resizer"
    assert ticket_data["column_id"] == "col_todo"
    assert ticket_data["status"] == "open"
    assert ticket_data["rank"].startswith("0|")
    ticket_id = ticket_data["ticket_id"]

    # 2. Get ticket detail
    resp_get = await client.get(f"/api/v1/tickets/{ticket_id}")
    assert resp_get.status_code == 200
    detail = resp_get.json()
    assert detail["ticket_id"] == ticket_id
    assert detail["title"] == "Build WebP Avatar Resizer"
    assert detail["subtask_count"] == 0


@pytest.mark.asyncio
async def test_ticket_move_and_status_sync(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # Create ticket 1
    t1_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Task 1", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    t1_id = t1_resp.json()["ticket_id"]

    # Create ticket 2
    t2_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Task 2", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    assert t2_resp.status_code == 201

    # Move ticket 1 to In Progress (stage='in_progress')
    move_resp = await client.patch(
        f"/api/v1/tickets/{t1_id}/move",
        json={"target_column_id": "col_in_progress"},
        headers=headers,
    )
    assert move_resp.status_code == 200
    moved_ticket = move_resp.json()
    assert moved_ticket["column_id"] == "col_in_progress"
    assert moved_ticket["status"] == "in_progress"

    # Move ticket 1 to Done (stage='done')
    done_resp = await client.patch(
        f"/api/v1/tickets/{t1_id}/move",
        json={"target_column_id": "col_done"},
        headers=headers,
    )
    assert done_resp.status_code == 200
    done_ticket = done_resp.json()
    assert done_ticket["column_id"] == "col_done"
    assert done_ticket["status"] == "done"


@pytest.mark.asyncio
async def test_ticket_lexorank_ordering(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # Create 3 tickets in To Do
    t1 = (await client.post(
        "/api/v1/tickets",
        json={"title": "First", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )).json()

    t2 = (await client.post(
        "/api/v1/tickets",
        json={"title": "Second", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )).json()

    t3 = (await client.post(
        "/api/v1/tickets",
        json={"title": "Third", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )).json()

    # Move t3 between t1 and t2
    move_resp = await client.patch(
        f"/api/v1/tickets/{t3['ticket_id']}/move",
        json={
            "target_column_id": "col_todo",
            "previous_ticket_id": t1["ticket_id"],
            "next_ticket_id": t2["ticket_id"],
        },
        headers=headers,
    )
    assert move_resp.status_code == 200
    t3_new = move_resp.json()
    assert t1["rank"] < t3_new["rank"] < t2["rank"]

    # Fetch board detail and check ordering in column
    board_resp = await client.get("/api/v1/boards/proj-core-engine")
    assert board_resp.status_code == 200
    board_data = board_resp.json()
    todo_col = next(c for c in board_data["columns"] if c["column_id"] == "col_todo")
    ticket_ids_in_order = [t["ticket_id"] for t in todo_col["tickets"]]
    assert ticket_ids_in_order == [t1["ticket_id"], t3["ticket_id"], t2["ticket_id"]]


@pytest.mark.asyncio
async def test_delegate_subtask(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # Create parent ticket
    p_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Implement Media Service", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    parent_id = p_resp.json()["ticket_id"]

    # Delegate subtask to agent_image_worker
    delegate_payload = {
        "target_actor_id": "agent_image_worker",
        "subtask_title": "WebP Avatar Resizing Module",
        "subtask_description": "Create 200x200 thumbnail function",
        "priority": "high",
        "labels": ["image", "worker"],
    }
    del_resp = await client.post(
        f"/api/v1/tickets/{parent_id}/delegate",
        json=delegate_payload,
        headers=headers,
    )
    assert del_resp.status_code == 201, del_resp.text
    subtask = del_resp.json()
    assert subtask["parent_ticket_id"] == parent_id
    assert subtask["assigned_to"] == "agent_image_worker"
    assert subtask["title"] == "WebP Avatar Resizing Module"

    # Verify parent ticket now has subtask stats and delegation comment
    parent_detail = (await client.get(f"/api/v1/tickets/{parent_id}")).json()
    assert parent_detail["subtask_count"] == 1
    assert parent_detail["subtask_done_count"] == 0
    assert len(parent_detail["comments"]) >= 1
    assert "agent_image_worker" in parent_detail["comments"][0]["content"]


@pytest.mark.asyncio
async def test_ticket_comments(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # Create ticket
    t_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Test Comments", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    t_id = t_resp.json()["ticket_id"]

    # Add public comment
    c1_resp = await client.post(
        f"/api/v1/tickets/{t_id}/comments",
        json={"content": "Please prioritize this ticket.", "is_internal": False},
        headers=headers,
    )
    assert c1_resp.status_code == 201
    assert c1_resp.json()["content"] == "Please prioritize this ticket."
    assert not c1_resp.json()["is_internal"]

    # Add agent internal reasoning comment
    agent_headers = auth_headers("agent_image_worker", "agent")
    c2_resp = await client.post(
        f"/api/v1/tickets/{t_id}/comments",
        json={
            "content": "Analyzing image buffer format: detected RGBA, will convert to RGB before WebP encoding.",
            "is_internal": True,
            "metadata": {"step": "image_analysis", "detected_channels": 4},
        },
        headers=agent_headers,
    )
    assert c2_resp.status_code == 201
    assert c2_resp.json()["is_internal"]
    assert c2_resp.json()["metadata"]["detected_channels"] == 4

    # Review flow: comments must be visible on ticket detail (web drawer source)
    detail = await client.get(f"/api/v1/tickets/{t_id}", headers=headers)
    assert detail.status_code == 200
    comments = detail.json().get("comments") or []
    assert len(comments) >= 2
    contents = {c["content"] for c in comments}
    assert "Please prioritize this ticket." in contents


@pytest.mark.asyncio
async def test_ticket_comment_validation_and_missing(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    t_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Comment validation", "board_id": "proj-core-engine", "column_id": "col_todo"},
        headers=headers,
    )
    t_id = t_resp.json()["ticket_id"]

    empty = await client.post(
        f"/api/v1/tickets/{t_id}/comments",
        json={"content": ""},
        headers=headers,
    )
    assert empty.status_code == 422

    missing = await client.post(
        "/api/v1/tickets/t_does_not_exist/comments",
        json={"content": "hello"},
        headers=headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_review_comment_from_human_pm(client: AsyncClient, auth_headers):
    """Human PM can leave a review note on a ticket in the review column."""
    headers = auth_headers("user_pm", "human")

    create = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Needs review",
            "board_id": "proj-core-engine",
            "column_id": "col_todo",
            "description": "Please review the WebP path",
        },
        headers=headers,
    )
    assert create.status_code == 201
    t_id = create.json()["ticket_id"]

    # Move into review if a review column exists on the default board
    board = await client.get("/api/v1/boards/proj-core-engine", headers=headers)
    assert board.status_code == 200
    review_col = next(
        (c for c in board.json()["columns"] if c.get("stage") == "review"),
        None,
    )
    if review_col:
        moved = await client.patch(
            f"/api/v1/tickets/{t_id}/move",
            json={"target_column_id": review_col["column_id"]},
            headers=headers,
        )
        assert moved.status_code == 200

    note = await client.post(
        f"/api/v1/tickets/{t_id}/comments",
        json={"content": "LGTM — approve after thumbnail smoke test.", "is_internal": False},
        headers=headers,
    )
    assert note.status_code == 201
    assert note.json()["actor_id"] == "user_pm"

    detail = await client.get(f"/api/v1/tickets/{t_id}", headers=headers)
    assert detail.status_code == 200
    comments = detail.json().get("comments") or []
    assert any("LGTM" in c["content"] for c in comments)
