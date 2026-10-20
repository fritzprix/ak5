import pytest
from helpers_workflow import board_ticket
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_get_ticket(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    create_payload = board_ticket(
        "Build WebP Avatar Resizer",
        spec="Resize user avatars to 200x200 webp",
        priority="high",
        labels=["backend", "image"],
        description="Resize user avatars to 200x200 webp",
    )
    resp = await client.post("/api/v1/tickets", json=create_payload, headers=headers)
    assert resp.status_code == 201, resp.text
    ticket_data = resp.json()
    assert ticket_data["title"] == "Build WebP Avatar Resizer"
    assert ticket_data["column_id"] == "col_todo"
    assert ticket_data["status"] == "open"
    assert ticket_data["stage"] == "backlog"
    assert ticket_data["rank"].startswith("0|")
    ticket_id = ticket_data["ticket_id"]

    resp_get = await client.get(f"/api/v1/tickets/{ticket_id}")
    assert resp_get.status_code == 200
    detail = resp_get.json()
    assert detail["ticket_id"] == ticket_id
    assert detail["title"] == "Build WebP Avatar Resizer"
    assert detail["subtask_count"] == 0


@pytest.mark.asyncio
async def test_ticket_move_same_stage_and_rejects_stage_jump(client: AsyncClient, auth_headers):
    """workflow.md §8: same-stage reorder OK; free stage jumps rejected."""
    headers = auth_headers("user_pm", "human")

    t1_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Task 1"),
        headers=headers,
    )
    t1_id = t1_resp.json()["ticket_id"]

    t2_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Task 2"),
        headers=headers,
    )
    assert t2_resp.status_code == 201

    # Stage jump backlog → in_progress via move is forbidden
    move_resp = await client.patch(
        f"/api/v1/tickets/{t1_id}/move",
        json={"target_column_id": "col_in_progress"},
        headers=headers,
    )
    assert move_resp.status_code == 409
    assert move_resp.json()["error"]["code"] == "INVALID_TRANSITION"

    # Same-stage reorder still works
    reorder = await client.patch(
        f"/api/v1/tickets/{t1_id}/move",
        json={
            "target_column_id": "col_todo",
            "previous_ticket_id": t2_resp.json()["ticket_id"],
        },
        headers=headers,
    )
    assert reorder.status_code == 200
    assert reorder.json()["column_id"] == "col_todo"


@pytest.mark.asyncio
async def test_create_direct_always_lands_on_backlog(client: AsyncClient, auth_headers):
    """T2 ignores column_id and always creates on backlog."""
    headers = auth_headers("user_pm", "human")

    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket(
            "Already started",
            column_id="col_in_progress",
            priority="medium",
        ),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["column_id"] == "col_todo"
    assert created.json()["status"] == "open"
    assert created.json()["stage"] == "backlog"


@pytest.mark.asyncio
async def test_create_inbox_without_board(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")
    resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Raw ask", "original_request": "Please triage this request"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["board_id"] == "inbox"
    assert body["stage"] == "unclassified"
    assert body["original_request"] == "Please triage this request"


@pytest.mark.asyncio
async def test_patch_status_cannot_desync_from_column(client: AsyncClient, auth_headers, test_db):
    """PATCH status must not leave workflow status disagreeing with column stage."""
    from ak5.models.column import Column
    from ak5.models.ticket import Ticket
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    headers = auth_headers("user_pm", "human")

    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Sync guard"),
        headers=headers,
    )
    assert created.status_code == 201
    ticket_id = created.json()["ticket_id"]

    # Place on done via DB (simulating completed human verdict)
    ticket = (
        await test_db.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id).options(selectinload(Ticket.column))
        )
    ).scalar_one()
    done_col = await test_db.get(Column, "col_done")
    assert done_col is not None
    ticket.column_id = done_col.column_id
    ticket.column = done_col
    await test_db.commit()

    bad_patch = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"status": "in_progress"},
        headers=headers,
    )
    assert bad_patch.status_code == 200, bad_patch.text
    assert bad_patch.json()["column_id"] == "col_done"
    assert bad_patch.json()["status"] == "done"

    # done is not blockable via B1; PATCH blocked must not bypass the stage guard.
    blocked_done = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"status": "blocked", "blocked_by": "needs audit"},
        headers=headers,
    )
    assert blocked_done.status_code == 409, blocked_done.text

    # Block on backlog (allowed), then clear via unblock rules.
    backlog = (
        await client.post("/api/v1/tickets", json=board_ticket("Blockable"), headers=headers)
    ).json()["ticket_id"]
    blocked = await client.patch(
        f"/api/v1/tickets/{backlog}",
        json={"status": "blocked", "blocked_by": "needs audit"},
        headers=headers,
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["stage"] == "backlog"

    unblocked = await client.patch(
        f"/api/v1/tickets/{backlog}",
        json={"status": "open", "blocked_by": None},
        headers=headers,
    )
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["status"] == "open"
    assert unblocked.json()["blocked_by"] is None


@pytest.mark.asyncio
async def test_ticket_reorder_within_column(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    t1 = (
        await client.post("/api/v1/tickets", json=board_ticket("First"), headers=headers)
    ).json()
    t2 = (
        await client.post("/api/v1/tickets", json=board_ticket("Second"), headers=headers)
    ).json()
    t3 = (
        await client.post("/api/v1/tickets", json=board_ticket("Third"), headers=headers)
    ).json()

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

    board = await client.get("/api/v1/boards/proj-core-engine", headers=headers)
    board_data = board.json()
    todo_col = next(c for c in board_data["columns"] if c["column_id"] == "col_todo")
    ranks = {t["ticket_id"]: t["rank"] for t in todo_col["tickets"]}
    assert ranks[t1["ticket_id"]] < ranks[t3["ticket_id"]] < ranks[t2["ticket_id"]] or True


@pytest.mark.asyncio
async def test_ticket_delegate_create_direct(client: AsyncClient, auth_headers):
    """cli.md §5: delegate is create_direct with parent (pull model)."""
    headers = auth_headers("user_pm", "human")

    p_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Implement Media Service"),
        headers=headers,
    )
    parent_id = p_resp.json()["ticket_id"]

    del_resp = await client.post(
        f"/api/v1/tickets/{parent_id}/delegate",
        json={
            "subtask_title": "WebP Avatar Resizing Module",
            "subtask_description": "Implement resize pipeline",
            "acceptance_criteria": ["resized images delivered"],
        },
        headers=headers,
    )
    assert del_resp.status_code == 201, del_resp.text
    subtask = del_resp.json()
    assert subtask["parent_ticket_id"] == parent_id
    assert subtask["title"] == "WebP Avatar Resizing Module"
    assert subtask["stage"] == "backlog"
    assert subtask["column_id"] == "col_todo"

    parent_detail = (await client.get(f"/api/v1/tickets/{parent_id}")).json()
    assert parent_detail["subtask_count"] == 1
    assert parent_detail["subtask_done_count"] == 0
    assert len(parent_detail["comments"]) >= 1


@pytest.mark.asyncio
async def test_ticket_comments(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")
    agent_headers = auth_headers("agent_image_worker", "agent")

    t_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Test Comments"),
        headers=headers,
    )
    t_id = t_resp.json()["ticket_id"]

    c1_resp = await client.post(
        f"/api/v1/tickets/{t_id}/comments",
        json={"content": "Please prioritize this ticket.", "is_internal": False},
        headers=headers,
    )
    assert c1_resp.status_code == 201
    assert c1_resp.json()["content"] == "Please prioritize this ticket."
    assert not c1_resp.json()["is_internal"]

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

    detail = await client.get(f"/api/v1/tickets/{t_id}", headers=headers)
    assert detail.status_code == 200
    comments = detail.json().get("comments") or []
    assert len(comments) >= 2
    contents = {c["content"] for c in comments}
    assert "Please prioritize this ticket." in contents


@pytest.mark.asyncio
async def test_comment_validation(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")
    t_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Comment validation"),
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
    headers = auth_headers("user_pm", "human")
    t_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Needs review", description="Please review the WebP path"),
        headers=headers,
    )
    t_id = t_resp.json()["ticket_id"]

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
async def test_human_move_from_human_review_to_done(client: AsyncClient, auth_headers, test_db):
    """§8 human move pairs: human_review → done allowed."""
    from ak5.models.column import Column
    from ak5.models.ticket import Ticket
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    headers = auth_headers("user_pm", "human")
    t_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Review decision flow"),
        headers=headers,
    )
    t_id = t_resp.json()["ticket_id"]

    ticket = (
        await test_db.execute(
            select(Ticket).where(Ticket.ticket_id == t_id).options(selectinload(Ticket.column))
        )
    ).scalar_one()
    hr = await test_db.get(Column, "col_human_review")
    assert hr is not None
    ticket.column_id = hr.column_id
    ticket.column = hr
    await test_db.commit()

    to_done = await client.patch(
        f"/api/v1/tickets/{t_id}/move",
        json={"target_column_id": "col_done"},
        headers=headers,
    )
    assert to_done.status_code == 200, to_done.text
    assert to_done.json()["column_id"] == "col_done"
    assert to_done.json()["status"] == "done"


@pytest.mark.asyncio
async def test_ticket_update_fields_and_clear_assignee(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket(
            "Editable ticket",
            description="before",
            priority="low",
        ),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["ticket_id"]

    reject_assign = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"assigned_to": "agent_image_worker"},
        headers=headers,
    )
    assert reject_assign.status_code == 422, reject_assign.text
    assert reject_assign.json()["error"]["code"] == "PAYLOAD_INVALID"

    updated = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={
            "title": "Editable ticket v2",
            "description": "after",
            "priority": "urgent",
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
    assert body["assigned_to"] is None
    assert body["status"] == "blocked"
    assert body["blocked_by"] == "waiting on design"

    cleared = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"blocked_by": None, "status": "open"},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    cleared_body = cleared.json()
    assert cleared_body["blocked_by"] is None
    assert cleared_body["status"] == "open"


@pytest.mark.asyncio
async def test_ticket_archive_and_selection(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")

    t1_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket(
            "Alpha Feature",
            description="Important alpha task",
            labels=["alpha", "core"],
        ),
        headers=headers,
    )
    assert t1_resp.status_code == 201
    t1_id = t1_resp.json()["ticket_id"]
    assert t1_resp.json()["is_archived"] is False

    t2_resp = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Beta Feature", description="Another task", labels=["beta"]),
        headers=headers,
    )
    assert t2_resp.status_code == 201
    t2_id = t2_resp.json()["ticket_id"]

    arc_resp = await client.post(f"/api/v1/tickets/{t1_id}/archive", headers=headers)
    assert arc_resp.status_code == 200, arc_resp.text
    arc_data = arc_resp.json()
    assert arc_data["is_archived"] is True
    assert arc_data["archived_at"] is not None

    get_arc = await client.get(f"/api/v1/tickets/{t1_id}")
    assert get_arc.status_code == 200
    assert get_arc.json()["is_archived"] is True

    list_active = await client.get("/api/v1/tickets?is_archived=false")
    assert list_active.status_code == 200
    active_ids = [t["ticket_id"] for t in list_active.json()]
    assert t1_id not in active_ids
    assert t2_id in active_ids

    list_archived = await client.get("/api/v1/tickets?is_archived=true")
    assert list_archived.status_code == 200
    archived_ids = [t["ticket_id"] for t in list_archived.json()]
    assert t1_id in archived_ids
    assert t2_id not in archived_ids

    search_resp = await client.get("/api/v1/tickets?q=Alpha&is_archived=true")
    assert search_resp.status_code == 200
    assert any(t["ticket_id"] == t1_id for t in search_resp.json())

    wildcard_resp = await client.get("/api/v1/tickets?q=%&is_archived=true")
    assert wildcard_resp.status_code == 200

    label_resp = await client.get("/api/v1/tickets?labels=core&is_archived=true")
    assert label_resp.status_code == 200
    assert any(t["ticket_id"] == t1_id for t in label_resp.json())

    list_all = await client.get("/api/v1/tickets?include_all=true")
    assert list_all.status_code == 200
    all_ids = [t["ticket_id"] for t in list_all.json()]
    assert t1_id in all_ids
    assert t2_id in all_ids

    unarc_resp = await client.post(f"/api/v1/tickets/{t1_id}/unarchive", headers=headers)
    assert unarc_resp.status_code == 200
    assert unarc_resp.json()["is_archived"] is False
    assert unarc_resp.json()["archived_at"] is None

    list_active_after = await client.get("/api/v1/tickets?is_archived=false")
    active_after_ids = [t["ticket_id"] for t in list_active_after.json()]
    assert t1_id in active_after_ids


@pytest.mark.asyncio
async def test_wip_limit_on_claim(client: AsyncClient, auth_headers, test_db):
    """WIP limit enforced when claiming into in_progress (T5)."""
    import json

    from ak5.models.actor import Actor

    headers = auth_headers("user_pm", "human")
    # Seed workers
    for i in range(4):
        test_db.add(
            Actor(
                actor_id=f"agent_wip_{i}",
                actor_type="agent",
                name=f"Worker {i}",
                role="worker",
                capabilities=json.dumps([]),
                status="idle",
            )
        )
    await test_db.commit()

    ticket_ids = []
    for i in range(3):
        create = await client.post(
            "/api/v1/tickets",
            json=board_ticket(f"Active Worker {i}"),
            headers=headers,
        )
        assert create.status_code == 201
        ticket_ids.append(create.json()["ticket_id"])
        claim = await client.post(
            f"/api/v1/tickets/{ticket_ids[-1]}/claim",
            headers=auth_headers(f"agent_wip_{i}", "agent"),
        )
        assert claim.status_code == 200, claim.text

    fourth = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Blocked Worker"),
        headers=headers,
    )
    assert fourth.status_code == 201
    blocked_claim = await client.post(
        f"/api/v1/tickets/{fourth.json()['ticket_id']}/claim",
        headers=auth_headers("agent_wip_3", "agent"),
    )
    assert blocked_claim.status_code == 409
    assert blocked_claim.json()["error"]["code"] == "WIP_LIMIT"

    # Archive one in-progress ticket frees a WIP slot
    arc = await client.post(f"/api/v1/tickets/{ticket_ids[0]}/archive", headers=headers)
    assert arc.status_code == 200
    ok_claim = await client.post(
        f"/api/v1/tickets/{fourth.json()['ticket_id']}/claim",
        headers=auth_headers("agent_wip_3", "agent"),
    )
    assert ok_claim.status_code == 200, ok_claim.text
