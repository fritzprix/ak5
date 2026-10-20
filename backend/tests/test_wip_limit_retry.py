"""#42 — WIP_LIMIT is retryable (exit 3); next returns 204 when WIP blocks claim."""

from __future__ import annotations

import json

import pytest
from ak5.contracts.errors import EXIT_CODE, ErrorCode
from ak5.models.actor import Actor
from ak5.models.event import Event
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy import select


@pytest.mark.asyncio
async def test_wip_limit_exit_code_is_retryable():
    assert EXIT_CODE[ErrorCode.WIP_LIMIT] == 3


@pytest.mark.asyncio
async def test_next_204_when_in_progress_wip_full(client: AsyncClient, auth_headers, test_db):
    headers = auth_headers("user_pm", "human")
    for i in range(3):
        test_db.add(
            Actor(
                actor_id=f"agent_wip42_{i}",
                actor_type="agent",
                name=f"W42 {i}",
                role="worker",
                capabilities=json.dumps([]),
                status="idle",
            )
        )
    await test_db.commit()

    claimed: list[str] = []
    for i in range(3):
        create = await client.post(
            "/api/v1/tickets",
            json=board_ticket(f"WIP fill {i}"),
            headers=headers,
        )
        assert create.status_code == 201, create.text
        tid = create.json()["ticket_id"]
        claim = await client.post(
            f"/api/v1/tickets/{tid}/claim",
            headers=auth_headers(f"agent_wip42_{i}", "agent"),
        )
        assert claim.status_code == 200, claim.text
        claimed.append(tid)

    waiting = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Waiting behind WIP"),
        headers=headers,
    )
    assert waiting.status_code == 201
    waiting_id = waiting.json()["ticket_id"]

    # next must not hand out a ticket that claim would reject with WIP_LIMIT
    nxt = await client.get(
        "/api/v1/boards/proj-core-engine/next",
        headers=auth_headers("agent_wip42_0", "agent"),
    )
    assert nxt.status_code == 204, nxt.text

    # Direct claim still returns WIP_LIMIT (retryable)
    claim = await client.post(
        f"/api/v1/tickets/{waiting_id}/claim",
        headers=auth_headers("agent_wip42_0", "agent"),
    )
    assert claim.status_code == 409
    assert claim.json()["error"]["code"] == "WIP_LIMIT"


@pytest.mark.asyncio
async def test_submit_frees_wip_emits_work_available_for_backlog(
    client: AsyncClient, auth_headers, test_db
):
    headers = auth_headers("user_pm", "human")
    for i in range(3):
        test_db.add(
            Actor(
                actor_id=f"agent_wip42b_{i}",
                actor_type="agent",
                name=f"W42b {i}",
                role="worker",
                capabilities=json.dumps([]),
                status="idle",
            )
        )
    await test_db.commit()

    tokens: dict[str, str] = {}
    for i in range(3):
        create = await client.post(
            "/api/v1/tickets",
            json=board_ticket(f"Fill {i}"),
            headers=headers,
        )
        tid = create.json()["ticket_id"]
        claim = await client.post(
            f"/api/v1/tickets/{tid}/claim",
            headers=auth_headers(f"agent_wip42b_{i}", "agent"),
        )
        assert claim.status_code == 200, claim.text
        tokens[tid] = claim.json()["claim_token"]

    waiting = await client.post(
        "/api/v1/tickets",
        json=board_ticket("After WIP free"),
        headers=headers,
    )
    waiting_id = waiting.json()["ticket_id"]

    before = (
        await test_db.execute(
            select(Event).where(
                Event.type == "WORK_AVAILABLE",
                Event.ticket_id == waiting_id,
            )
        )
    ).scalars().all()
    before_n = len(before)

    # Free one in_progress slot via submit
    first_id = next(iter(tokens))
    submit = await client.post(
        f"/api/v1/tickets/{first_id}/submit",
        json={
            "claim_token": tokens[first_id],
            "deliverable": {"summary": "done", "artifacts": []},
        },
        headers=auth_headers("agent_wip42b_0", "agent"),
    )
    assert submit.status_code == 200, submit.text

    await test_db.commit()
    after = (
        await test_db.execute(
            select(Event).where(
                Event.type == "WORK_AVAILABLE",
                Event.ticket_id == waiting_id,
            )
        )
    ).scalars().all()
    assert len(after) > before_n

    nxt = await client.get(
        "/api/v1/boards/proj-core-engine/next",
        headers=auth_headers("agent_wip42b_0", "agent"),
    )
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["ticket_id"] == waiting_id
