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


@pytest.mark.asyncio
async def test_review_approve_and_request_changes_flow(client: AsyncClient, auth_headers):
    """Web review UX: comment then move to Done, or comment then send back to In Progress."""
    headers = auth_headers("user_pm", "human")

    create = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Review decision flow",
            "board_id": "proj-core-engine",
            "column_id": "col_todo",
        },
        headers=headers,
    )
    assert create.status_code == 201
    t_id = create.json()["ticket_id"]

    to_review = await client.patch(
        f"/api/v1/tickets/{t_id}/move",
        json={"target_column_id": "col_review"},
        headers=headers,
    )
    assert to_review.status_code == 200
    assert to_review.json()["column_id"] == "col_review"

    # Approve path: structured note + Done
    approve_note = await client.post(
        f"/api/v1/tickets/{t_id}/comments",
        json={"content": "✅ Approved\nLGTM after smoke test", "is_internal": False},
        headers=headers,
    )
    assert approve_note.status_code == 201

    to_done = await client.patch(
        f"/api/v1/tickets/{t_id}/move",
        json={"target_column_id": "col_done"},
        headers=headers,
    )
    assert to_done.status_code == 200
    assert to_done.json()["column_id"] == "col_done"
    assert to_done.json()["status"] == "done"

    # Second ticket: request changes → In Progress
    create2 = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Needs changes",
            "board_id": "proj-core-engine",
            "column_id": "col_todo",
        },
        headers=headers,
    )
    t2 = create2.json()["ticket_id"]
    await client.patch(
        f"/api/v1/tickets/{t2}/move",
        json={"target_column_id": "col_review"},
        headers=headers,
    )
    changes = await client.post(
        f"/api/v1/tickets/{t2}/comments",
        json={"content": "↩️ Changes requested\nAdd unit tests for edge cases", "is_internal": False},
        headers=headers,
    )
    assert changes.status_code == 201
    back = await client.patch(
        f"/api/v1/tickets/{t2}/move",
        json={"target_column_id": "col_in_progress"},
        headers=headers,
    )
    assert back.status_code == 200
    assert back.json()["column_id"] == "col_in_progress"
    assert back.json()["status"] == "in_progress"

    detail = await client.get(f"/api/v1/tickets/{t2}", headers=headers)
    assert any("Changes requested" in c["content"] for c in detail.json().get("comments") or [])


@pytest.mark.asyncio
async def test_ticket_update_fields_and_clear_assignee(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    created = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Editable ticket",
            "description": "before",
            "board_id": "proj-core-engine",
            "column_id": "col_todo",
            "priority": "low",
            "assigned_to": "user_pm",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["ticket_id"]

    updated = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={
            "title": "Editable ticket v2",
            "description": "after",
            "priority": "urgent",
            "assigned_to": "agent_image_worker",
            "status": "blocked",
            "blocked_by": "waiting on design",
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["title"] == "Editable ticket v2"
    assert body["description"] == "after"
    assert body["priority"] == "urgent"
    assert body["assigned_to"] == "agent_image_worker"
    assert body["status"] == "blocked"
    assert body["blocked_by"] == "waiting on design"

    cleared = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"assigned_to": None, "blocked_by": None, "status": "open"},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    cleared_body = cleared.json()
    assert cleared_body["assigned_to"] is None
    assert cleared_body["blocked_by"] is None
    assert cleared_body["status"] == "open"

    # Empty string must normalize to NULL (actor FK must never store "")
    empty_assign = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"assigned_to": "user_pm"},
        headers=headers,
    )
    assert empty_assign.status_code == 200
    cleared_via_empty = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"assigned_to": ""},
        headers=headers,
    )
    assert cleared_via_empty.status_code == 200, cleared_via_empty.text
    assert cleared_via_empty.json()["assigned_to"] is None

    missing = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"assigned_to": "actor_does_not_exist"},
        headers=headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_ticket_archive_and_selection(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # 1. Create two tickets
    t1_resp = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Alpha Feature",
            "description": "Important alpha task",
            "board_id": "proj-core-engine",
            "column_id": "col_todo",
            "labels": ["alpha", "core"],
        },
        headers=headers,
    )
    assert t1_resp.status_code == 201
    t1_id = t1_resp.json()["ticket_id"]
    assert t1_resp.json()["is_archived"] is False

    t2_resp = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Beta Feature",
            "description": "Another task",
            "board_id": "proj-core-engine",
            "column_id": "col_todo",
            "labels": ["beta"],
        },
        headers=headers,
    )
    assert t2_resp.status_code == 201
    t2_id = t2_resp.json()["ticket_id"]

    # 2. Archive t1
    arc_resp = await client.post(f"/api/v1/tickets/{t1_id}/archive", headers=headers)
    assert arc_resp.status_code == 200, arc_resp.text
    arc_data = arc_resp.json()
    assert arc_data["is_archived"] is True
    assert arc_data["archived_at"] is not None

    # Direct lookup still works on archived ticket
    get_arc = await client.get(f"/api/v1/tickets/{t1_id}")
    assert get_arc.status_code == 200
    assert get_arc.json()["is_archived"] is True

    # 3. List active only (default)
    list_active = await client.get("/api/v1/tickets?is_archived=false")
    assert list_active.status_code == 200
    active_ids = [t["ticket_id"] for t in list_active.json()]
    assert t1_id not in active_ids
    assert t2_id in active_ids

    # 4. List archived only
    list_archived = await client.get("/api/v1/tickets?is_archived=true")
    assert list_archived.status_code == 200
    archived_ids = [t["ticket_id"] for t in list_archived.json()]
    assert t1_id in archived_ids
    assert t2_id not in archived_ids

    # 5. Search with query and labels
    search_resp = await client.get("/api/v1/tickets?q=Alpha&is_archived=true")
    assert search_resp.status_code == 200
    assert any(t["ticket_id"] == t1_id for t in search_resp.json())

    # Search with % wildcard does not fail or misbehave
    wildcard_resp = await client.get("/api/v1/tickets?q=%&is_archived=true")
    assert wildcard_resp.status_code == 200

    label_resp = await client.get("/api/v1/tickets?labels=core&is_archived=true")
    assert label_resp.status_code == 200
    assert any(t["ticket_id"] == t1_id for t in label_resp.json())

    # 5.1 Test include_all returns both
    list_all = await client.get("/api/v1/tickets?include_all=true")
    assert list_all.status_code == 200
    all_ids = [t["ticket_id"] for t in list_all.json()]
    assert t1_id in all_ids
    assert t2_id in all_ids

    # 6. Unarchive t1
    unarc_resp = await client.post(f"/api/v1/tickets/{t1_id}/unarchive", headers=headers)
    assert unarc_resp.status_code == 200
    assert unarc_resp.json()["is_archived"] is False
    assert unarc_resp.json()["archived_at"] is None

    # Now t1 appears in active list again
    list_active_after = await client.get("/api/v1/tickets?is_archived=false")
    active_after_ids = [t["ticket_id"] for t in list_active_after.json()]
    assert t1_id in active_after_ids


@pytest.mark.asyncio
async def test_wip_limit_frees_slot_on_archive(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    # 1. Fill In Progress to WIP limit (3)
    ticket_ids = []
    for i in range(3):
        create = await client.post(
            "/api/v1/tickets",
            json={
                "title": f"Active Worker {i}",
                "board_id": "proj-core-engine",
                "column_id": "col_in_progress",
            },
            headers=headers,
        )
        assert create.status_code == 201
        ticket_ids.append(create.json()["ticket_id"])

    # 2. 4th ticket must be blocked by WIP limit (409)
    blocked_resp = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Blocked Worker",
            "board_id": "proj-core-engine",
            "column_id": "col_in_progress",
        },
        headers=headers,
    )
    assert blocked_resp.status_code == 409

    # 3. Archive one of the tickets in col_in_progress
    arc_resp = await client.post(f"/api/v1/tickets/{ticket_ids[0]}/archive", headers=headers)
    assert arc_resp.status_code == 200

    # 4. Now adding the 4th ticket must SUCCEED because the archived ticket freed a WIP slot!
    success_resp = await client.post(
        "/api/v1/tickets",
        json={
            "title": "Now Allowed Worker",
            "board_id": "proj-core-engine",
            "column_id": "col_in_progress",
        },
        headers=headers,
    )
    assert success_resp.status_code == 201, success_resp.text
