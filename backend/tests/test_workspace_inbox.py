"""Issue #76: every workspace uses its own inbox (identity.md §7, workflow.md §2.1)."""

from __future__ import annotations

import json

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.bridge import register_bridge
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.inbox import inbox_board_id_for, is_inbox_board_id, resolve_board_alias
from ak5.server.models.actor import Actor
from ak5.server.models.workspace import Agent, User
from ak5.server.seed import ensure_local_tenancy
from ak5.server.services.workspaces import create_workspace
from helpers_workflow import inbox_ticket
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def test_inbox_helpers():
    assert inbox_board_id_for(None) == "inbox"
    assert inbox_board_id_for("ws_local") == "inbox"
    assert inbox_board_id_for("ws_b") == "ws_b__inbox"
    assert resolve_board_alias("inbox", "ws_b") == "ws_b__inbox"
    assert resolve_board_alias("proj-x", "ws_b") == "proj-x"
    assert is_inbox_board_id("inbox") and is_inbox_board_id("ws_b__inbox")
    assert not is_inbox_board_id("proj-core-engine")


async def _actor_alias(db: AsyncSession, principal_id: str, actor_type: str, role: str) -> None:
    if await db.get(Actor, principal_id) is None:
        db.add(
            Actor(
                actor_id=principal_id,
                actor_type=actor_type,
                name=principal_id,
                role=role,
                capabilities="[]",
                status="idle",
            )
        )
    register_bridge(principal_id, principal_id)


async def _workspace_b(db: AsyncSession) -> dict[str, dict[str, str] | str]:
    """Workspace B via the real service (creates B's inbox) + owner and three agents."""
    await ensure_local_tenancy(db)
    owner = User(user_id="usr_b_owner", email="b@example.com", display_name="B owner")
    db.add(owner)
    await db.flush()
    ws, inbox_id = await create_workspace(db, name="B", owner=owner)
    await _actor_alias(db, owner.user_id, "human", "human")
    headers: dict[str, dict[str, str] | str] = {"ws": ws.workspace_id, "inbox": inbox_id}
    headers["owner"] = {
        "Authorization": "Bearer "
        + issue_access_token(
            Principal(
                principal_id=owner.user_id,
                principal_type="user",
                workspace_id=ws.workspace_id,
                workflow_role=Role.HUMAN,
                membership_role="owner",
            )
        )
    }
    for role in (Role.TRIAGE, Role.WORKER, Role.GATEKEEPER):
        agent_id = f"agt_b_{role.value}"
        db.add(
            Agent(
                agent_id=agent_id,
                workspace_id=ws.workspace_id,
                name=agent_id,
                workflow_role=role.value,
                capabilities="[]",
                board_scope="[]",
                created_by=owner.user_id,
            )
        )
        await _actor_alias(db, agent_id, "agent", role.value)
        headers[role.value] = {
            "Authorization": "Bearer "
            + issue_access_token(
                Principal(
                    principal_id=agent_id,
                    principal_type="agent",
                    workspace_id=ws.workspace_id,
                    workflow_role=role,
                )
            )
        }
    await db.commit()
    return headers


@pytest.mark.asyncio
async def test_non_local_workspace_t1_route_reroute_bounce_use_own_inbox(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    b = await _workspace_b(test_db)
    owner, triage, worker, gk = b["owner"], b["triage"], b["worker"], b["gatekeeper"]
    b_inbox = b["inbox"]
    assert b_inbox == f"{b['ws']}__inbox"

    # A ws_local inbox ticket that B must never see.
    local = await client.post(
        "/api/v1/tickets",
        json=inbox_ticket("Local secret"),
        headers=auth_headers("user_pm", "human"),
    )
    assert local.status_code == 201, local.text
    assert local.json()["board_id"] == "inbox"
    local_tid = local.json()["ticket_id"]

    # T1 in B → B's inbox (was 403 WORKSPACE_FORBIDDEN).
    created = await client.post("/api/v1/tickets", json=inbox_ticket("B request"), headers=owner)
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]
    assert created.json()["board_id"] == b_inbox
    assert created.json()["stage"] == "unclassified"

    # `inbox` alias resolves to the caller's inbox everywhere.
    nxt = await client.get("/api/v1/boards/inbox/next", headers=triage)
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["ticket_id"] == tid
    board = await client.get("/api/v1/boards/inbox", headers=owner)
    assert board.status_code == 200, board.text
    assert board.json()["board_id"] == b_inbox
    listed = await client.get("/api/v1/tickets", params={"board_id": "inbox"}, headers=owner)
    ids = {t["ticket_id"] for t in listed.json()}
    assert tid in ids and local_tid not in ids
    polled = await client.get("/api/v1/events", params={"board_id": "inbox"}, headers=owner)
    assert polled.status_code == 200, polled.text
    assert polled.json()["events"]
    assert {e["board_id"] for e in polled.json()["events"]} == {b_inbox}
    sub = await client.post(
        "/api/v1/subscriptions",
        json={"filter": {"board_id": "inbox"}, "start": "now"},
        headers=triage,
    )
    assert sub.status_code in (200, 201), sub.text
    assert sub.json()["filter"]["board_id"] == b_inbox

    # Triage routes onto a B board.
    made = await client.post(
        "/api/v1/boards",
        json={"board_id": "b-proj", "name": "B project", "template": "default"},
        headers=owner,
    )
    assert made.status_code in (200, 201), made.text
    token = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)).json()["claim_token"]
    routed = await client.post(
        f"/api/v1/tickets/{tid}/route",
        json={
            "claim_token": token,
            "plan": {
                "refined_title": "B work",
                "refined_spec": "do it",
                "acceptance_criteria": ["done"],
                "target": {"board_id": "b-proj"},
                "confidence": 0.9,
            },
        },
        headers=triage,
    )
    assert routed.status_code == 200, routed.text
    assert routed.json()["board_id"] == "b-proj"

    # T10 bounce → B's inbox.
    wtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)).json()["claim_token"]
    bounced = await client.post(
        f"/api/v1/tickets/{tid}/bounce",
        json={"claim_token": wtoken, "reason": "not mine"},
        headers=worker,
    )
    assert bounced.status_code == 200, bounced.text
    assert bounced.json()["board_id"] == b_inbox

    # Route again, submit, then T14 misrouted → B's inbox.
    token = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)).json()["claim_token"]
    await client.post(
        f"/api/v1/tickets/{tid}/route",
        json={
            "claim_token": token,
            "plan": {
                "refined_title": "B work",
                "refined_spec": "do it",
                "acceptance_criteria": ["done"],
                "target": {"board_id": "b-proj"},
                "confidence": 0.9,
            },
        },
        headers=triage,
    )
    wtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)).json()["claim_token"]
    submitted = await client.post(
        f"/api/v1/tickets/{tid}/submit",
        json={
            "claim_token": wtoken,
            "deliverable": {"summary": "done", "artifacts": [], "criteria_self_check": []},
        },
        headers=worker,
    )
    assert submitted.status_code == 200, submitted.text
    gtoken = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)).json()["claim_token"]
    misrouted = await client.post(
        f"/api/v1/tickets/{tid}/verdict",
        json={
            "claim_token": gtoken,
            "verdict": {"decision": "reject", "misrouted": True, "reason": "wrong board", "findings": []},
        },
        headers=gk,
    )
    assert misrouted.status_code == 200, misrouted.text
    assert misrouted.json()["board_id"] == b_inbox

    # T21 reroute (human) → B's inbox.
    token = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)).json()["claim_token"]
    await client.post(
        f"/api/v1/tickets/{tid}/route",
        json={
            "claim_token": token,
            "plan": {
                "refined_title": "B work",
                "refined_spec": "do it",
                "acceptance_criteria": ["done"],
                "target": {"board_id": "b-proj"},
                "confidence": 0.9,
            },
        },
        headers=triage,
    )
    rerouted = await client.post(
        f"/api/v1/tickets/{tid}/reroute",
        json={"reason": "rethink", "to_inbox": True},
        headers=owner,
    )
    assert rerouted.status_code == 200, rerouted.text
    assert rerouted.json()["board_id"] == b_inbox

    # ws_local's inbox ticket stays invisible to B.
    hidden = await client.get(f"/api/v1/tickets/{local_tid}", headers=owner)
    assert hidden.status_code == 403


@pytest.mark.asyncio
async def test_board_create_rejects_inbox_style_ids(client: AsyncClient, test_db: AsyncSession):
    b = await _workspace_b(test_db)
    for board_id in ("inbox", "ws_other__inbox", f"{b['ws']}__inbox"):
        res = await client.post(
            "/api/v1/boards",
            json={"board_id": board_id, "name": "x", "template": "default"},
            headers=b["owner"],
        )
        assert res.status_code == 403, (board_id, res.text)
        assert json.loads(res.text)["error"]["code"] == "SYSTEM_BOARD"


@pytest.mark.asyncio
async def test_scoped_agent_keeps_access_to_its_own_workspace_inbox(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    """#82 review: board_scope always exempts the agent's *own* workspace inbox."""
    b = await _workspace_b(test_db)
    scoped_id = "agt_b_scoped_triage"
    test_db.add(
        Agent(
            agent_id=scoped_id,
            workspace_id=b["ws"],
            name=scoped_id,
            workflow_role=Role.TRIAGE.value,
            capabilities="[]",
            board_scope=json.dumps(["b-x"]),  # does not list the inbox
            created_by="usr_b_owner",
        )
    )
    await _actor_alias(test_db, scoped_id, "agent", Role.TRIAGE.value)
    await test_db.commit()
    scoped = {
        "Authorization": "Bearer "
        + issue_access_token(
            Principal(
                principal_id=scoped_id,
                principal_type="agent",
                workspace_id=b["ws"],
                workflow_role=Role.TRIAGE,
            )
        )
    }

    local = await client.post(
        "/api/v1/tickets", json=inbox_ticket("Local inbox"), headers=auth_headers("user_pm", "human")
    )
    assert local.status_code == 201, local.text
    mine = await client.post("/api/v1/tickets", json=inbox_ticket("B inbox"), headers=b["owner"])
    assert mine.status_code == 201, mine.text
    mine_id = mine.json()["ticket_id"]

    nxt = await client.get("/api/v1/boards/inbox/next", headers=scoped)
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["ticket_id"] == mine_id

    claimed = await client.post(f"/api/v1/tickets/{mine_id}/claim", headers=scoped)
    assert claimed.status_code == 200, claimed.text

    foreign = await client.post(
        f"/api/v1/tickets/{local.json()['ticket_id']}/claim", headers=scoped
    )
    assert foreign.status_code == 403, foreign.text


@pytest.mark.asyncio
async def test_boot_warns_about_inbox_like_standard_board(test_db: AsyncSession, caplog):
    """#82 review (optional): pre-#82 boards named *__inbox are flagged at boot."""
    from ak5.server.models.board import Board
    from ak5.server.seed import warn_inbox_like_standard_boards

    await ensure_local_tenancy(test_db)
    test_db.add(
        Board(
            board_id="legacy__inbox",
            workspace_id="ws_local",
            name="Old",
            created_by="user_pm",
            kind="standard",
            template="default",
        )
    )
    await test_db.commit()
    with caplog.at_level("WARNING", logger="ak5.server.seed"):
        flagged = await warn_inbox_like_standard_boards(test_db)
    assert flagged == ["legacy__inbox"]
    assert "legacy__inbox" in caplog.text
