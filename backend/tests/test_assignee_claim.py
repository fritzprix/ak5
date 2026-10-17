"""#30 — assigned_to tracks the current claim holder only."""

from __future__ import annotations

import json

import pytest
from ak5.models.actor import Actor
from ak5.models.audit import AuditLog
from ak5.models.ticket import Ticket
from ak5.services.workflow import clear_assignees_without_claim
from helpers_workflow import board_ticket, inbox_ticket
from httpx import AsyncClient
from sqlalchemy import select


@pytest.mark.asyncio
async def test_claim_sets_assigned_to_release_clears(client: AsyncClient, auth_headers, test_db):
    headers = auth_headers("user_pm", "human")
    test_db.add(
        Actor(
            actor_id="agent_asg_1",
            actor_type="agent",
            name="Asg1",
            role="worker",
            capabilities=json.dumps([]),
            status="idle",
        )
    )
    await test_db.commit()

    create = await client.post("/api/v1/tickets", json=board_ticket("Claim assignee"), headers=headers)
    assert create.status_code == 201
    tid = create.json()["ticket_id"]
    assert create.json().get("assigned_to") in (None, "")

    claim = await client.post(
        f"/api/v1/tickets/{tid}/claim",
        headers=auth_headers("agent_asg_1", "agent"),
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["ticket"]["assigned_to"] == "agent_asg_1"
    token = claim.json()["claim_token"]

    release = await client.post(
        f"/api/v1/tickets/{tid}/release",
        json={"claim_token": token, "reason": "stop"},
        headers=auth_headers("agent_asg_1", "agent"),
    )
    assert release.status_code == 200, release.text
    assert release.json()["assigned_to"] is None


@pytest.mark.asyncio
async def test_submit_and_route_clear_assigned_to(client: AsyncClient, auth_headers, test_db):
    headers = auth_headers("user_pm", "human")
    for actor_id, role in (("agent_asg_w", "worker"), ("agent_asg_t", "triage")):
        test_db.add(
            Actor(
                actor_id=actor_id,
                actor_type="agent",
                name=actor_id,
                role=role,
                capabilities=json.dumps([]),
                status="idle",
            )
        )
    await test_db.commit()

    # submit path
    create = await client.post("/api/v1/tickets", json=board_ticket("Submit clears"), headers=headers)
    tid = create.json()["ticket_id"]
    claim = await client.post(
        f"/api/v1/tickets/{tid}/claim",
        headers=auth_headers("agent_asg_w", "agent"),
    )
    token = claim.json()["claim_token"]
    submit = await client.post(
        f"/api/v1/tickets/{tid}/submit",
        json={"claim_token": token, "deliverable": {"summary": "x", "artifacts": []}},
        headers=auth_headers("agent_asg_w", "agent"),
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["assigned_to"] is None

    # route path
    inbox = await client.post(
        "/api/v1/tickets",
        json=inbox_ticket("Route clears assignee"),
        headers=headers,
    )
    assert inbox.status_code == 201, inbox.text
    inbox_id = inbox.json()["ticket_id"]
    claim_t = await client.post(
        f"/api/v1/tickets/{inbox_id}/claim",
        headers=auth_headers("agent_asg_t", "agent"),
    )
    assert claim_t.status_code == 200, claim_t.text
    assert claim_t.json()["ticket"]["assigned_to"] == "agent_asg_t"
    route = await client.post(
        f"/api/v1/tickets/{inbox_id}/route",
        json={
            "claim_token": claim_t.json()["claim_token"],
            "plan": {
                "refined_title": "Routed",
                "refined_spec": "spec",
                "acceptance_criteria": ["done"],
                "confidence": 0.9,
                "target": {"board_id": "proj-core-engine"},
            },
        },
        headers=auth_headers("agent_asg_t", "agent"),
    )
    assert route.status_code == 200, route.text
    assert route.json()["assigned_to"] is None


@pytest.mark.asyncio
async def test_startup_clears_stale_assignee_with_audit(test_db):
    from ak5.models.column import Column

    col = await test_db.get(Column, "col_todo")
    assert col is not None
    stale = Ticket(
        ticket_id="TK-STALE-ASG",
        board_id="proj-core-engine",
        column_id=col.column_id,
        title="Stale assignee",
        priority="medium",
        rank="0|zzzzzz:",
        labels="[]",
        assigned_to="user_pm",
        created_by="user_pm",
        status="open",
    )
    stale.column = col
    test_db.add(stale)
    await test_db.commit()

    n = await clear_assignees_without_claim(test_db)
    await test_db.commit()
    assert n >= 1
    await test_db.refresh(stale)
    assert stale.assigned_to is None

    audits = (
        await test_db.execute(
            select(AuditLog).where(
                AuditLog.target_id == "TK-STALE-ASG",
            )
        )
    ).scalars().all()
    assert any("clear_stale_assignee" in (a.payload or "") for a in audits)


@pytest.mark.asyncio
async def test_create_and_patch_reject_assigned_to(client: AsyncClient, auth_headers, test_db):
    headers = auth_headers("user_pm", "human")
    test_db.add(
        Actor(
            actor_id="agent_asg_rej",
            actor_type="agent",
            name="Reject",
            role="worker",
            capabilities=json.dumps([]),
            status="idle",
        )
    )
    await test_db.commit()

    bad_create = await client.post(
        "/api/v1/tickets",
        json=board_ticket("No assignee on create", assigned_to="user_pm"),
        headers=headers,
    )
    assert bad_create.status_code == 422, bad_create.text
    assert bad_create.json()["error"]["code"] == "PAYLOAD_INVALID"

    ok = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Claim sets assignee"),
        headers=headers,
    )
    assert ok.status_code == 201
    tid = ok.json()["ticket_id"]

    bad_patch = await client.patch(
        f"/api/v1/tickets/{tid}",
        json={"assigned_to": "agent_asg_rej"},
        headers=headers,
    )
    assert bad_patch.status_code == 422
    assert bad_patch.json()["error"]["code"] == "PAYLOAD_INVALID"

    claim = await client.post(
        f"/api/v1/tickets/{tid}/claim",
        headers=auth_headers("agent_asg_rej", "agent"),
    )
    assert claim.status_code == 200
    assert claim.json()["ticket"]["assigned_to"] == "agent_asg_rej"
    token = claim.json()["claim_token"]

    release = await client.post(
        f"/api/v1/tickets/{tid}/release",
        json={"claim_token": token, "reason": "done testing"},
        headers=auth_headers("agent_asg_rej", "agent"),
    )
    assert release.status_code == 200
    assert release.json()["assigned_to"] is None
