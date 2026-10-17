"""Issue #10: workflow state machine + transition API regression tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.models.actor import Actor
from ak5.models.event import Event
from ak5.models.ticket import Ticket
from ak5.services.workflow import TRANSITION_TABLE
from helpers_workflow import board_ticket, inbox_ticket
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_roles(db: AsyncSession) -> None:
    actors = [
        Actor(
            actor_id="agent_triage",
            actor_type="agent",
            name="Triage Bot",
            role="triage",
            capabilities=json.dumps([]),
            status="idle",
        ),
        Actor(
            actor_id="agent_worker",
            actor_type="agent",
            name="Worker Bot",
            role="worker",
            capabilities=json.dumps(["python"]),
            status="idle",
        ),
        Actor(
            actor_id="agent_worker_b",
            actor_type="agent",
            name="Worker B",
            role="worker",
            capabilities=json.dumps(["python"]),
            status="idle",
        ),
        Actor(
            actor_id="agent_gk",
            actor_type="agent",
            name="Gatekeeper",
            role="gatekeeper",
            capabilities=json.dumps([]),
            status="idle",
        ),
        Actor(
            actor_id="agent_gk_b",
            actor_type="agent",
            name="Gatekeeper B",
            role="gatekeeper",
            capabilities=json.dumps([]),
            status="idle",
        ),
    ]
    for a in actors:
        if await db.get(Actor, a.actor_id) is None:
            db.add(a)
    await db.commit()


def _h(auth_headers, actor_id: str, actor_type: str = "agent") -> dict[str, str]:
    return auth_headers(actor_id, actor_type)


@pytest.fixture
async def roles(test_db: AsyncSession, auth_headers):
    await _seed_roles(test_db)
    return auth_headers


# ---------------------------------------------------------------------------
# Transition table coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_table_ops_present():
    ops = {r.op for r in TRANSITION_TABLE}
    for required in (
        "create",
        "create_direct",
        "claim",
        "route",
        "heartbeat",
        "release",
        "submit",
        "bounce",
        "verdict_approve",
        "verdict_reject",
        "verdict_reject_misrouted",
        "reopen",
        "submit_postmortem",
        "reroute",
        "block",
        "unblock",
    ):
        assert required in ops


@pytest.mark.parametrize(
    "spec",
    [pytest.param(s, id=f"{s.note or s.op}:{s.from_stage}") for s in TRANSITION_TABLE if s.from_stage is not None],
)
def test_transition_table_rows_have_roles(spec):
    assert spec.roles
    assert all(isinstance(r, Role) for r in spec.roles)


@pytest.mark.asyncio
async def test_happy_path_t1_through_t15(client: AsyncClient, roles, test_db: AsyncSession):
    """Drive T1→T3→T4→T5→T9→T11→T12→T15 (core path to done)."""
    human = _h(roles, "user_pm", "human")
    triage = _h(roles, "agent_triage")
    worker = _h(roles, "agent_worker")
    gk = _h(roles, "agent_gk")

    # T1
    created = await client.post(
        "/api/v1/tickets",
        json=inbox_ticket("Please build auth", original_request="Please build auth"),
        headers=human,
    )
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]
    assert created.json()["board_id"] == "inbox"
    assert created.json()["stage"] == "unclassified"

    # T3 claim triage
    claim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)
    assert claim.status_code == 200, claim.text
    token = claim.json()["claim_token"]

    # T4 route
    routed = await client.post(
        f"/api/v1/tickets/{tid}/route",
        json={
            "claim_token": token,
            "plan": {
                "refined_title": "Build auth",
                "refined_spec": "Implement login",
                "acceptance_criteria": ["users can log in"],
                "target": {"board_id": "proj-core-engine"},
                "confidence": 0.9,
            },
        },
        headers=triage,
    )
    assert routed.status_code == 200, routed.text
    assert routed.json()["board_id"] == "proj-core-engine"
    assert routed.json()["stage"] == "backlog"

    # T5 claim worker
    wclaim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
    assert wclaim.status_code == 200, wclaim.text
    wtoken = wclaim.json()["claim_token"]
    assert wclaim.json()["ticket"]["stage"] == "in_progress"
    assert wclaim.json()["revision"] is not None

    # T6 heartbeat
    hb = await client.post(
        f"/api/v1/tickets/{tid}/heartbeat",
        json={"claim_token": wtoken},
        headers=worker,
    )
    assert hb.status_code == 200

    # T9 submit
    submitted = await client.post(
        f"/api/v1/tickets/{tid}/submit",
        json={
            "claim_token": wtoken,
            "deliverable": {
                "summary": "Done",
                "artifacts": [{"kind": "commit", "ref": "abc"}],
                "criteria_self_check": [{"criterion": "users can log in", "pass": True}],
            },
        },
        headers=worker,
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["stage"] == "agent_review"

    # T11 claim gk
    gclaim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)
    assert gclaim.status_code == 200, gclaim.text
    gtoken = gclaim.json()["claim_token"]

    # T12 approve (findings required)
    approved = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={
            "claim_token": gtoken,
            "verdict": {
                "decision": "approve",
                "findings": [{"criterion": "users can log in", "pass": True, "note": "ok"}],
            },
        },
        headers=gk,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["stage"] == "human_review"

    # T15 human approve → done
    done = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={"verdict": {"decision": "approve", "reason": "ship it"}},
        headers=human,
    )
    assert done.status_code == 200, done.text
    assert done.json()["stage"] == "done"
    assert done.json()["status"] == "done"

    # History
    hist = await client.get(f"/api/v1/tickets/{tid}/history", headers=human)
    assert hist.status_code == 200
    body = hist.json()
    assert body["revisions"]
    assert body["reviews"]
    assert body["audit"]


@pytest.mark.asyncio
async def test_gatekeeper_approve_requires_findings(client: AsyncClient, roles):
    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")
    gk = _h(roles, "agent_gk")

    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("Findings required"), headers=human)
    ).json()["ticket_id"]
    wtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)).json()["claim_token"]
    await client.post(
        f"/api/v1/tickets/{tid}/submit",
        json={
            "claim_token": wtoken,
            "deliverable": {"summary": "x", "artifacts": [], "criteria_self_check": []},
        },
        headers=worker,
    )
    gtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)).json()["claim_token"]
    bad = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={"claim_token": gtoken, "verdict": {"decision": "approve", "findings": []}},
        headers=gk,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == ErrorCode.PAYLOAD_INVALID.value


@pytest.mark.asyncio
async def test_separation_of_duties(client: AsyncClient, roles, test_db: AsyncSession):
    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")

    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("SoD"), headers=human)
    ).json()["ticket_id"]
    wtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)).json()["claim_token"]
    await client.post(
        f"/api/v1/tickets/{tid}/submit",
        json={
            "claim_token": wtoken,
            "deliverable": {"summary": "x", "artifacts": [], "criteria_self_check": []},
        },
        headers=worker,
    )
    # Same actor switches to gatekeeper role — still forbidden to review own submission.
    actor = await test_db.get(Actor, "agent_worker")
    assert actor is not None
    actor.role = "gatekeeper"
    await test_db.commit()

    sod = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
    assert sod.status_code == 403
    assert sod.json()["error"]["code"] == ErrorCode.SEPARATION_OF_DUTIES.value


@pytest.mark.asyncio
async def test_reject_escalation_e1(client: AsyncClient, roles):
    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")
    gk = _h(roles, "agent_gk")

    tid = (
        await client.post(
            "/api/v1/tickets",
            json=board_ticket("Escalate me", max_attempts=2),
            headers=human,
        )
    ).json()["ticket_id"]

    for _ in range(2):
        wtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)).json()[
            "claim_token"
        ]
        await client.post(
            f"/api/v1/tickets/{tid}/submit",
            json={
                "claim_token": wtoken,
                "deliverable": {"summary": "try", "artifacts": [], "criteria_self_check": []},
            },
            headers=worker,
        )
        gtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)).json()[
            "claim_token"
        ]
        rejected = await client.post(
            f"/api/v1/tickets/{tid}/verdict",
            json={
                "claim_token": gtoken,
                "verdict": {
                    "decision": "reject",
                    "findings": [{"criterion": "x", "pass": False, "note": "fail"}],
                },
            },
            headers=gk,
        )
        assert rejected.status_code == 200, rejected.text

    body = rejected.json()
    assert body["attempt"] >= 2
    assert body["blocked_kind"] == "escalation"
    assert body["status"] == "blocked"

    # Cannot claim while escalated
    claim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
    assert claim.status_code == 423
    assert claim.json()["error"]["code"] == ErrorCode.TICKET_BLOCKED.value


@pytest.mark.asyncio
async def test_concurrent_claim_one_wins(client: AsyncClient, roles, test_db: AsyncSession):
    human = _h(roles, "user_pm", "human")
    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("Race"), headers=human)
    ).json()["ticket_id"]

    from ak5.main import app

    async def claim_as(actor_id: str) -> tuple[int, dict]:
        transport = ASGITransport(app=app)
        token_headers = _h(roles, actor_id)
        async with AsyncClient(transport=transport, base_url="http://test", headers=token_headers) as ac:
            resp = await ac.post(f"/api/v1/tickets/{tid}/claim")
            try:
                body = resp.json()
            except Exception:
                body = {}
            return resp.status_code, body if isinstance(body, dict) else {}

    results = await asyncio.gather(claim_as("agent_worker"), claim_as("agent_worker_b"))
    statuses = sorted(s for s, _ in results)
    assert statuses == [200, 409]
    loser = next(body for status, body in results if status == 409)
    assert loser["error"]["code"] == ErrorCode.CLAIM_CONFLICT.value

    # Sequential: after a completed claim (in_progress + valid lease), loser → CLAIM_CONFLICT
    # (not INVALID_TRANSITION). Separate ticket avoids :memory: StaticPool race corruption.
    tid2 = (
        await client.post("/api/v1/tickets", json=board_ticket("Race late"), headers=human)
    ).json()["ticket_id"]
    won = await client.post(f"/api/v1/tickets/{tid2}/claim", headers=_h(roles, "agent_worker"))
    assert won.status_code == 200, won.text
    assert won.json()["ticket"]["stage"] == "in_progress"
    assert won.json()["claim_token"]
    late = await client.post(
        f"/api/v1/tickets/{tid2}/claim",
        headers=_h(roles, "agent_worker_b"),
    )
    assert late.status_code == 409, late.text
    assert late.json()["error"]["code"] == ErrorCode.CLAIM_CONFLICT.value


@pytest.mark.asyncio
async def test_idempotency_key_create_once(client: AsyncClient, roles):
    human = _h(roles, "user_pm", "human")
    headers = {**human, "Idempotency-Key": "idem-create-1"}
    r1 = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Idempotent"),
        headers=headers,
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Idempotent"),
        headers=headers,
    )
    assert r2.status_code == 201
    assert r1.json()["ticket_id"] == r2.json()["ticket_id"]

    listed = await client.get("/api/v1/tickets?q=Idempotent", headers=human)
    matches = [t for t in listed.json() if t["title"] == "Idempotent"]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_release_bounce_block_unblock_reopen_reroute(client: AsyncClient, roles):
    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")
    triage = _h(roles, "agent_triage")

    # bounce path: create direct → claim → bounce to inbox
    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("Bounce me"), headers=human)
    ).json()["ticket_id"]
    wtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)).json()["claim_token"]
    bounced = await client.post(
        f"/api/v1/tickets/{tid}/bounce",
        json={"claim_token": wtoken, "reason": "wrong board"},
        headers=worker,
    )
    assert bounced.status_code == 200
    assert bounced.json()["board_id"] == "inbox"

    # triage claim + block needs_human
    token = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)).json()["claim_token"]
    blocked = await client.post(
        f"/api/v1/tickets/{tid}/block",
        json={"kind": "needs_human", "reason": "unclear ask", "claim_token": token},
        headers=triage,
    )
    assert blocked.status_code == 200
    assert blocked.json()["blocked_kind"] == "needs_human"

    unblocked = await client.post(
        f"/api/v1/tickets/{tid}/unblock",
        json={"resolution": "clarified: build auth"},
        headers=human,
    )
    assert unblocked.status_code == 200
    assert unblocked.json()["blocked_kind"] is None

    # release path
    tid2 = (
        await client.post("/api/v1/tickets", json=board_ticket("Release me"), headers=human)
    ).json()["ticket_id"]
    wtoken2 = (await client.post(f"/api/v1/tickets/{tid2}/claim", headers=worker)).json()[
        "claim_token"
    ]
    released = await client.post(
        f"/api/v1/tickets/{tid2}/release",
        json={"claim_token": wtoken2, "reason": "need break"},
        headers=worker,
    )
    assert released.status_code == 200
    assert released.json()["stage"] == "backlog"

    # reroute to inbox
    rerouted = await client.post(
        f"/api/v1/tickets/{tid2}/reroute",
        json={"reason": "wrong place", "to_inbox": True},
        headers=human,
    )
    assert rerouted.status_code == 200
    assert rerouted.json()["board_id"] == "inbox"

    # reopen: finish a ticket then reopen
    tid3 = (
        await client.post("/api/v1/tickets", json=board_ticket("Reopen me"), headers=human)
    ).json()["ticket_id"]
    # shortcut to human_review via worker+gk path abbreviated: place via verdict after submit
    wtoken3 = (await client.post(f"/api/v1/tickets/{tid3}/claim", headers=worker)).json()[
        "claim_token"
    ]
    await client.post(
        f"/api/v1/tickets/{tid3}/submit",
        json={
            "claim_token": wtoken3,
            "deliverable": {"summary": "x", "artifacts": [], "criteria_self_check": []},
        },
        headers=worker,
    )
    gk = _h(roles, "agent_gk")
    gtoken = (await client.post(f"/api/v1/tickets/{tid3}/claim", headers=gk)).json()["claim_token"]
    await client.post(
        f"/api/v1/tickets/{tid3}/verdict",
        json={
            "claim_token": gtoken,
            "verdict": {
                "decision": "approve",
                "findings": [{"criterion": "c", "pass": True}],
            },
        },
        headers=gk,
    )
    await client.post(
        f"/api/v1/tickets/{tid3}/verdict",
        json={"verdict": {"decision": "approve"}},
        headers=human,
    )
    reopened = await client.post(
        f"/api/v1/tickets/{tid3}/reopen",
        json={"reason": "found a bug"},
        headers=human,
    )
    assert reopened.status_code == 200
    assert reopened.json()["stage"] == "postmortem_pending"


@pytest.mark.asyncio
async def test_postmortem_and_misrouted(client: AsyncClient, roles):
    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")
    gk = _h(roles, "agent_gk")

    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("PM path"), headers=human)
    ).json()["ticket_id"]
    wtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)).json()["claim_token"]
    await client.post(
        f"/api/v1/tickets/{tid}/submit",
        json={
            "claim_token": wtoken,
            "deliverable": {"summary": "x", "artifacts": [], "criteria_self_check": []},
        },
        headers=worker,
    )
    gtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)).json()["claim_token"]
    await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={
            "claim_token": gtoken,
            "verdict": {
                "decision": "approve",
                "findings": [{"criterion": "c", "pass": True}],
            },
        },
        headers=gk,
    )
    # human reject → postmortem_pending
    await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={"verdict": {"decision": "reject", "reason": "not what we wanted"}},
        headers=human,
    )
    gtoken2 = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)).json()["claim_token"]
    pm = await client.post(
        f"/api/v1/tickets/{tid}/postmortem",
        json={
            "claim_token": gtoken2,
            "postmortem": {
                "what_went_wrong": "misunderstood",
                "root_cause": "vague spec",
                "revised_spec": "Clearer spec",
                "revised_acceptance_criteria": ["clearer criterion"],
            },
        },
        headers=gk,
    )
    assert pm.status_code == 200, pm.text
    assert pm.json()["stage"] == "backlog"

    # misrouted reject
    tid2 = (
        await client.post("/api/v1/tickets", json=board_ticket("Misrouted"), headers=human)
    ).json()["ticket_id"]
    wtoken2 = (await client.post(f"/api/v1/tickets/{tid2}/claim", headers=worker)).json()[
        "claim_token"
    ]
    await client.post(
        f"/api/v1/tickets/{tid2}/submit",
        json={
            "claim_token": wtoken2,
            "deliverable": {"summary": "x", "artifacts": [], "criteria_self_check": []},
        },
        headers=worker,
    )
    gtoken3 = (await client.post(f"/api/v1/tickets/{tid2}/claim", headers=gk)).json()["claim_token"]
    mis = await client.post(
        f"/api/v1/tickets/{tid2}/verdict",
        json={
            "claim_token": gtoken3,
            "verdict": {"decision": "reject", "misrouted": True, "reason": "wrong board"},
        },
        headers=gk,
    )
    assert mis.status_code == 200
    assert mis.json()["board_id"] == "inbox"


@pytest.mark.asyncio
async def test_next_and_lease_reclaim(client: AsyncClient, roles, test_db: AsyncSession):
    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")

    empty = await client.get("/api/v1/boards/proj-core-engine/next", headers=worker)
    assert empty.status_code == 204

    t1 = (
        await client.post("/api/v1/tickets", json=board_ticket("Oldest"), headers=human)
    ).json()["ticket_id"]
    await client.post("/api/v1/tickets", json=board_ticket("Newer"), headers=human)

    nxt = await client.get("/api/v1/boards/proj-core-engine/next", headers=worker)
    assert nxt.status_code == 200
    assert nxt.json()["ticket_id"] == t1

    # Claim then expire lease → reclaim on next
    claim = await client.post(f"/api/v1/tickets/{t1}/claim", headers=worker)
    assert claim.status_code == 200
    ticket = await test_db.get(Ticket, t1)
    assert ticket is not None
    ticket.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    await test_db.commit()

    nxt2 = await client.get("/api/v1/boards/proj-core-engine/next", headers=worker)
    assert nxt2.status_code == 200
    assert nxt2.json()["ticket_id"] == t1
    ticket2 = await test_db.get(Ticket, t1)
    await test_db.refresh(ticket2)
    assert ticket2.claim_token is None
    assert ticket2.lease_expirations >= 1


@pytest.mark.asyncio
async def test_forbidden_role_on_claim(client: AsyncClient, roles):
    human = _h(roles, "user_pm", "human")
    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("No claim"), headers=human)
    ).json()["ticket_id"]
    # human cannot claim backlog (worker only)
    bad = await client.post(f"/api/v1/tickets/{tid}/claim", headers=human)
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == ErrorCode.ROLE_FORBIDDEN.value


@pytest.mark.asyncio
async def test_route_create_board_respects_max_auto_boards(client: AsyncClient, roles, monkeypatch):
    from ak5.config import settings

    monkeypatch.setattr(settings, "MAX_AUTO_BOARDS", 1)
    human = _h(roles, "user_pm", "human")
    triage = _h(roles, "agent_triage")

    async def _route_new(name: str) -> int:
        tid = (
            await client.post(
                "/api/v1/tickets",
                json=inbox_ticket(name),
                headers=human,
            )
        ).json()["ticket_id"]
        token = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)).json()[
            "claim_token"
        ]
        resp = await client.post(
            f"/api/v1/tickets/{tid}/route",
            json={
                "claim_token": token,
                "plan": {
                    "refined_title": name,
                    "refined_spec": "spec",
                    "acceptance_criteria": ["c"],
                    "target": {
                        "create_board": {
                            "name": name,
                            "template": "default",
                            "description": "",
                        }
                    },
                    "confidence": 0.5,
                },
            },
            headers=triage,
        )
        return resp.status_code

    assert await _route_new("Auto One") == 200
    assert await _route_new("Auto Two") == 422


@pytest.mark.asyncio
async def test_work_available_events_emitted(client: AsyncClient, roles, test_db: AsyncSession):
    human = _h(roles, "user_pm", "human")
    await client.post("/api/v1/tickets", json=inbox_ticket("Event check"), headers=human)
    rows = (
        await test_db.execute(select(Event).where(Event.type == "WORK_AVAILABLE"))
    ).scalars().all()
    assert rows


# ---------------------------------------------------------------------------
# Orchestrator audit round-1 regressions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_with_human_role_cannot_t15_to_done(client: AsyncClient, roles, test_db: AsyncSession):
    """High: role=human on actor_type=agent must not unlock T15 (done is human-only)."""
    from ak5.models.column import Column
    from ak5.models.ticket import Ticket as TicketModel
    from ak5.services.ticket_status import derive_status
    from sqlalchemy.orm import selectinload

    human = _h(roles, "user_pm", "human")
    spoof = Actor(
        actor_id="agent_spoof_human",
        actor_type="agent",
        name="Spoof",
        role="human",
        capabilities=json.dumps([]),
        status="idle",
    )
    if await test_db.get(Actor, spoof.actor_id) is None:
        test_db.add(spoof)
        await test_db.commit()

    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("Guard done"), headers=human)
    ).json()["ticket_id"]
    ticket = (
        await test_db.execute(
            select(TicketModel)
            .where(TicketModel.ticket_id == tid)
            .options(selectinload(TicketModel.column))
        )
    ).scalar_one()
    hr = await test_db.get(Column, "col_human_review")
    assert hr is not None
    ticket.column_id = hr.column_id
    ticket.column = hr
    ticket.status = derive_status(ticket)
    await test_db.commit()

    spoof_headers = _h(roles, "agent_spoof_human", "agent")
    verdict = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={"verdict": {"decision": "approve"}},
        headers=spoof_headers,
    )
    assert verdict.status_code == 403, verdict.text
    assert verdict.json()["error"]["code"] == ErrorCode.ROLE_FORBIDDEN.value
    ticket2 = await test_db.get(TicketModel, tid)
    await test_db.refresh(ticket2)
    assert ticket2.status != "done"


@pytest.mark.asyncio
async def test_ticket_mutations_persist_event_log_rows(client: AsyncClient, roles, test_db: AsyncSession):
    """Medium: create/update/archive/move/delegate/comment/attachment write events rows."""
    import io

    human = _h(roles, "user_pm", "human")

    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Event log create"),
        headers=human,
    )
    assert created.status_code == 201
    tid = created.json()["ticket_id"]
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "TICKET_CREATED", Event.ticket_id == tid)
        )
    ).scalars().first()

    updated = await client.patch(
        f"/api/v1/tickets/{tid}",
        json={"title": "Event log updated"},
        headers=human,
    )
    assert updated.status_code == 200
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "TICKET_UPDATED", Event.ticket_id == tid)
        )
    ).scalars().first()

    archived = await client.post(f"/api/v1/tickets/{tid}/archive", headers=human)
    assert archived.status_code == 200
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "TICKET_ARCHIVED", Event.ticket_id == tid)
        )
    ).scalars().first()

    unarchived = await client.post(f"/api/v1/tickets/{tid}/unarchive", headers=human)
    assert unarchived.status_code == 200
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "TICKET_UNARCHIVED", Event.ticket_id == tid)
        )
    ).scalars().first()

    tid2 = (
        await client.post("/api/v1/tickets", json=board_ticket("Move me"), headers=human)
    ).json()["ticket_id"]
    tid3 = (
        await client.post("/api/v1/tickets", json=board_ticket("Anchor"), headers=human)
    ).json()["ticket_id"]
    moved = await client.patch(
        f"/api/v1/tickets/{tid2}/move",
        json={"target_column_id": "col_todo", "previous_ticket_id": tid3},
        headers=human,
    )
    assert moved.status_code == 200
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "TICKET_MOVED", Event.ticket_id == tid2)
        )
    ).scalars().first()

    delegated = await client.post(
        f"/api/v1/tickets/{tid}/delegate",
        json={
            "subtask_title": "Child",
            "subtask_description": "child work",
            "acceptance_criteria": ["ok"],
        },
        headers=human,
    )
    assert delegated.status_code == 201
    child_id = delegated.json()["ticket_id"]
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "TICKET_DELEGATED", Event.ticket_id == child_id)
        )
    ).scalars().first()

    comment = await client.post(
        f"/api/v1/tickets/{tid}/comments",
        json={"content": "hello event", "is_internal": False},
        headers=human,
    )
    assert comment.status_code == 201
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "COMMENT_ADDED", Event.ticket_id == tid)
        )
    ).scalars().first()

    upload = await client.post(
        f"/api/v1/tickets/{tid}/attachments",
        files={"file": ("note.txt", io.BytesIO(b"hi"), "text/plain")},
        headers=human,
    )
    assert upload.status_code == 201, upload.text
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "ATTACHMENT_ADDED", Event.ticket_id == tid)
        )
    ).scalars().first()
    att_id = upload.json()["attachment_id"]
    deleted = await client.delete(f"/api/v1/tickets/{tid}/attachments/{att_id}", headers=human)
    assert deleted.status_code == 200
    assert (
        await test_db.execute(
            select(Event).where(Event.type == "ATTACHMENT_DELETED", Event.ticket_id == tid)
        )
    ).scalars().first()


@pytest.mark.asyncio
async def test_move_t16_requires_reason_and_records_review(
    client: AsyncClient, roles, test_db: AsyncSession
):
    """Medium: human_review→postmortem_pending move needs reason + review row."""
    from ak5.models.column import Column
    from ak5.models.ticket import Ticket as TicketModel
    from ak5.models.ticket_review import TicketReview
    from ak5.services.ticket_status import derive_status
    from sqlalchemy.orm import selectinload

    human = _h(roles, "user_pm", "human")
    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("Reject via drag"), headers=human)
    ).json()["ticket_id"]
    ticket = (
        await test_db.execute(
            select(TicketModel)
            .where(TicketModel.ticket_id == tid)
            .options(selectinload(TicketModel.column))
        )
    ).scalar_one()
    hr = await test_db.get(Column, "col_human_review")
    assert hr is not None
    ticket.column_id = hr.column_id
    ticket.column = hr
    ticket.status = derive_status(ticket)
    await test_db.commit()

    missing = await client.patch(
        f"/api/v1/tickets/{tid}/move",
        json={"target_column_id": "col_postmortem_pending"},
        headers=human,
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["code"] == ErrorCode.PAYLOAD_INVALID.value

    ok = await client.patch(
        f"/api/v1/tickets/{tid}/move",
        json={
            "target_column_id": "col_postmortem_pending",
            "reason": "needs clearer acceptance criteria",
        },
        headers=human,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["stage"] == "postmortem_pending"
    review = (
        await test_db.execute(
            select(TicketReview).where(
                TicketReview.ticket_id == tid,
                TicketReview.stage == "human_review",
                TicketReview.decision == "reject",
            )
        )
    ).scalars().first()
    assert review is not None
    assert review.reason == "needs clearer acceptance criteria"


@pytest.mark.asyncio
async def test_patch_blocked_releases_claim_and_moves_to_backlog(
    client: AsyncClient, roles, test_db: AsyncSession
):
    """Medium: PATCH status=blocked on claimed in_progress must run B1 side effects."""
    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")
    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("Claim then PATCH block"), headers=human)
    ).json()["ticket_id"]
    claim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
    assert claim.status_code == 200
    assert claim.json()["ticket"]["stage"] == "in_progress"
    assert claim.json()["claim_token"]

    blocked = await client.patch(
        f"/api/v1/tickets/{tid}",
        json={"status": "blocked", "blocked_by": "waiting on vendor"},
        headers=human,
    )
    assert blocked.status_code == 200, blocked.text
    body = blocked.json()
    assert body["status"] == "blocked"
    assert body["stage"] == "backlog"
    assert "claim_token" not in body
    assert body["claimed_by"] is None
    assert body["blocked_kind"] == "external"
    assert body["blocked_by"] == "waiting on vendor"


@pytest.mark.asyncio
async def test_idempotency_fingerprint_rejects_different_ticket_body(
    client: AsyncClient, roles
):
    """Low: same Idempotency-Key + op with different body/ticket → PAYLOAD_INVALID."""
    human = _h(roles, "user_pm", "human")
    headers = {**human, "Idempotency-Key": "idem-fp-cross"}
    r1 = await client.post(
        "/api/v1/tickets",
        json=board_ticket("First fingerprint"),
        headers=headers,
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Second fingerprint"),
        headers=headers,
    )
    assert r2.status_code == 422, r2.text
    assert r2.json()["error"]["code"] == ErrorCode.PAYLOAD_INVALID.value


@pytest.mark.asyncio
async def test_lease_reclaim_audits_as_system_actor(
    client: AsyncClient, roles, test_db: AsyncSession
):
    """Low: T8/T20 reclaim audit uses seeded system actor, not user_pm."""
    from ak5.models.audit import AuditLog
    from ak5.seed import SYSTEM_ACTOR_ID

    human = _h(roles, "user_pm", "human")
    worker = _h(roles, "agent_worker")
    # Ensure system actor exists even if this DB was seeded before the seed change.
    if await test_db.get(Actor, SYSTEM_ACTOR_ID) is None:
        test_db.add(
            Actor(
                actor_id=SYSTEM_ACTOR_ID,
                actor_type="agent",
                name="System",
                role="system",
                capabilities=json.dumps([]),
                status="idle",
            )
        )
        await test_db.commit()

    tid = (
        await client.post("/api/v1/tickets", json=board_ticket("Expire me"), headers=human)
    ).json()["ticket_id"]
    claim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
    assert claim.status_code == 200
    ticket = await test_db.get(Ticket, tid)
    assert ticket is not None
    ticket.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    await test_db.commit()

    nxt = await client.get("/api/v1/boards/proj-core-engine/next", headers=worker)
    assert nxt.status_code == 200

    rows = (
        await test_db.execute(
            select(AuditLog)
            .where(AuditLog.target_id == tid, AuditLog.action == "TRANSITION")
            .order_by(AuditLog.log_id.desc())
        )
    ).scalars().all()
    reclaim = [a for a in rows if a.payload and "lease_expire" in a.payload]
    assert reclaim, [(a.actor_id, a.payload) for a in rows[:5]]
    assert all(a.actor_id == SYSTEM_ACTOR_ID for a in reclaim)
    assert all(a.actor_id != "user_pm" for a in reclaim)
