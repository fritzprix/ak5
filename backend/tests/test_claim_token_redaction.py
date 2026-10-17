"""#47 — claim_token only on claim response, never on ticket reads/events/audit."""

from __future__ import annotations

import json

import pytest
from ak5.models.actor import Actor
from ak5.models.audit import AuditLog
from ak5.models.event import Event
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy import select


@pytest.mark.asyncio
async def test_claim_token_not_leaked_on_reads(client: AsyncClient, auth_headers, test_db):
    headers = auth_headers("user_pm", "human")
    test_db.add(
        Actor(
            actor_id="agent_tok_a",
            actor_type="agent",
            name="Tok A",
            role="worker",
            capabilities=json.dumps([]),
            status="idle",
        )
    )
    test_db.add(
        Actor(
            actor_id="agent_tok_b",
            actor_type="agent",
            name="Tok B",
            role="worker",
            capabilities=json.dumps([]),
            status="idle",
        )
    )
    await test_db.commit()

    create = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Secret claim"),
        headers=headers,
    )
    assert create.status_code == 201
    tid = create.json()["ticket_id"]

    claim = await client.post(
        f"/api/v1/tickets/{tid}/claim",
        headers=auth_headers("agent_tok_a", "agent"),
    )
    assert claim.status_code == 200, claim.text
    token = claim.json()["claim_token"]
    assert token
    assert "claim_token" not in claim.json()["ticket"]

    # Other actor must not see the token in any ticket/board JSON.
    other = auth_headers("agent_tok_b", "agent")
    detail = await client.get(f"/api/v1/tickets/{tid}", headers=other)
    assert detail.status_code == 200
    assert token not in detail.text
    assert "claim_token" not in detail.json()

    listed = await client.get("/api/v1/tickets", headers=other)
    assert token not in listed.text

    board = await client.get("/api/v1/boards/proj-core-engine", headers=other)
    assert board.status_code == 200
    assert token not in board.text

    # Events / audit must not store the token string.
    await test_db.commit()
    events = (await test_db.execute(select(Event))).scalars().all()
    for ev in events:
        blob = json.dumps(ev.data) if isinstance(ev.data, dict) else str(ev.data or "")
        assert token not in blob, ev.type
    audits = (await test_db.execute(select(AuditLog))).scalars().all()
    for row in audits:
        assert token not in (row.payload or "")
