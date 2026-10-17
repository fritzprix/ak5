"""Full workflow E2E with reject / postmortem loops (#29)."""

from __future__ import annotations

import pytest
from ak5.contracts.errors import ErrorCode
from ak5.models.actor import Actor
from helpers_workflow import inbox_ticket
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _h(roles, actor_id: str, actor_type: str = "agent"):
    return roles(actor_id, actor_type)


async def _subscribe(
    client: AsyncClient,
    headers: dict,
    board_id: str | None,
    *,
    start: str = "now",
) -> str:
    payload: dict = {
        "filter": {"board_id": board_id, "events": ["WORK_AVAILABLE"]},
        "start": start,
    }
    res = await client.post("/api/v1/subscriptions", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["subscription_id"]


async def _wait_work_available(
    client: AsyncClient,
    headers: dict,
    sub_id: str,
    *,
    ticket_id: str | None = None,
) -> dict:
    res = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 2, "limit": 20},
        headers={**headers, "X-AK5-Consumer": f"e2e-{sub_id}"},
    )
    assert res.status_code == 200, res.text
    events = res.json()["events"]
    assert events, f"expected WORK_AVAILABLE on {sub_id}"
    assert all(e["type"] == "WORK_AVAILABLE" for e in events)
    if ticket_id is not None:
        assert any(e.get("ticket_id") == ticket_id for e in events), events
    return res.json()


@pytest.mark.asyncio
async def test_e2e_reject_postmortem_to_done(client: AsyncClient, roles, test_db: AsyncSession):
    """inbox → route(+auto board) → worker → gk reject → retry → approve → human reject →
    postmortem → retry → approve → human approve → done."""
    human = _h(roles, "user_pm", "human")
    triage = _h(roles, "agent_triage")
    worker = _h(roles, "agent_worker")
    gk = _h(roles, "agent_gk")

    inbox_sub = await _subscribe(client, triage, "inbox")

    created = await client.post(
        "/api/v1/tickets",
        json=inbox_ticket("E2E gate request", original_request="Build the gate"),
        headers=human,
    )
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]

    await _wait_work_available(client, triage, inbox_sub, ticket_id=tid)

    # Role forgery: worker cannot claim unclassified
    bad = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == ErrorCode.ROLE_FORBIDDEN.value

    claim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)
    token = claim.json()["claim_token"]
    board_name = f"e2e-gate-{tid}"
    routed = await client.post(
        f"/api/v1/tickets/{tid}/route",
        json={
            "claim_token": token,
            "plan": {
                "refined_title": "Build gate",
                "refined_spec": "Implement regression gate",
                "acceptance_criteria": ["tests pass in CI"],
                "target": {
                    "create_board": {
                        "name": board_name,
                        "template": "default",
                        "description": "auto board for e2e gate",
                    }
                },
                "confidence": 0.95,
            },
        },
        headers=triage,
    )
    assert routed.status_code == 200, routed.text
    board_id = routed.json()["board_id"]
    assert board_id != "inbox"
    assert routed.json()["stage"] == "backlog"

    # start=earliest: WORK_AVAILABLE was emitted during route before this sub existed
    worker_sub = await _subscribe(client, worker, board_id, start="earliest")
    await _wait_work_available(client, worker, worker_sub, ticket_id=tid)

    async def _worker_submit(note: str) -> None:
        wclaim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
        assert wclaim.status_code == 200, wclaim.text
        wtoken = wclaim.json()["claim_token"]
        submitted = await client.post(
            f"/api/v1/tickets/{tid}/submit",
            json={
                "claim_token": wtoken,
                "deliverable": {
                    "summary": note,
                    "artifacts": [{"kind": "commit", "ref": "abc"}],
                    "criteria_self_check": [{"criterion": "tests pass in CI", "pass": True}],
                },
            },
            headers=worker,
        )
        assert submitted.status_code == 200, submitted.text

    await _worker_submit("first attempt")

    # SoD: even with gatekeeper role, submitter cannot claim own work for review
    worker_actor = await test_db.get(Actor, "agent_worker")
    assert worker_actor is not None
    worker_actor.role = "gatekeeper"
    await test_db.commit()
    sod = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
    assert sod.status_code == 403
    assert sod.json()["error"]["code"] == ErrorCode.SEPARATION_OF_DUTIES.value
    worker_actor.role = "worker"
    await test_db.commit()

    gk_sub = await _subscribe(client, gk, board_id, start="earliest")
    await _wait_work_available(client, gk, gk_sub, ticket_id=tid)

    gclaim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)
    gtoken = gclaim.json()["claim_token"]
    rejected = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={
            "claim_token": gtoken,
            "verdict": {
                "decision": "reject",
                "findings": [
                    {"criterion": "tests pass in CI", "pass": False, "note": "missing coverage"}
                ],
                "reason": "need more tests",
            },
        },
        headers=gk,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["stage"] == "backlog"

    await _wait_work_available(client, worker, worker_sub, ticket_id=tid)
    await _worker_submit("second attempt")

    gclaim2 = await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)
    approved = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={
            "claim_token": gclaim2.json()["claim_token"],
            "verdict": {
                "decision": "approve",
                "findings": [
                    {"criterion": "tests pass in CI", "pass": True, "note": "ok"}
                ],
            },
        },
        headers=gk,
    )
    assert approved.status_code == 200
    assert approved.json()["stage"] == "human_review"

    # Human reject → postmortem_pending
    human_reject = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={"verdict": {"decision": "reject", "reason": "docs incomplete"}},
        headers=human,
    )
    assert human_reject.status_code == 200, human_reject.text
    assert human_reject.json()["stage"] == "postmortem_pending"

    # Postmortem → backlog
    gclaim3 = await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)
    pm = await client.post(
        f"/api/v1/tickets/{tid}/postmortem",
        json={
            "claim_token": gclaim3.json()["claim_token"],
            "postmortem": {
                "what_went_wrong": "Missed docs",
                "root_cause": "rushed review",
                "revised_spec": "Implement regression gate with docs",
                "revised_acceptance_criteria": ["tests pass in CI", "docs updated"],
            },
        },
        headers=gk,
    )
    assert pm.status_code == 200, pm.text
    assert pm.json()["stage"] == "backlog"

    history = await client.get(f"/api/v1/tickets/{tid}/history", headers=human)
    assert history.status_code == 200
    assert len(history.json()["postmortems"]) >= 1

    await _wait_work_available(client, worker, worker_sub, ticket_id=tid)
    await _worker_submit("third attempt")
    gclaim4 = await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)
    await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={
            "claim_token": gclaim4.json()["claim_token"],
            "verdict": {
                "decision": "approve",
                "findings": [{"criterion": "tests pass in CI", "pass": True}],
            },
        },
        headers=gk,
    )
    done = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={"verdict": {"decision": "approve", "reason": "ship"}},
        headers=human,
    )
    assert done.status_code == 200, done.text
    assert done.json()["stage"] == "done"
    assert done.json()["status"] == "done"

    comment = await client.post(
        f"/api/v1/tickets/{tid}/comments",
        json={"content": "closed via regression gate", "is_internal": False},
        headers=human,
    )
    assert comment.status_code == 201, comment.text
