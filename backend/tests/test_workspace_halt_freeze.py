"""Issue #199: workspace halt and freeze flags and signals."""

from __future__ import annotations

import pytest
from ak5.contracts.stages import Role, Stage
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.models.board import Board
from ak5.server.models.column import Column
from ak5.server.models.ticket import Ticket
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
from ak5.server.seed import LOCAL_AGENT_WORKER_ID, LOCAL_USER_ID, ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _user_headers(
    user_id: str = LOCAL_USER_ID,
    *,
    workspace_id: str = LOCAL_WORKSPACE_ID,
    membership_role: str = "owner",
) -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=user_id,
            principal_type="user",
            workspace_id=workspace_id,
            workflow_role=Role.HUMAN,
            membership_role=membership_role,
        )
    )
    return {"Authorization": f"Bearer {token}"}


def _agent_headers(role: Role = Role.WORKER) -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=LOCAL_AGENT_WORKER_ID,
            principal_type="agent",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=role,
        )
    )
    return {"Authorization": f"Bearer {token}"}


async def _setup_board_with_columns(db: AsyncSession, board_id: str = "brd_hf") -> str:
    await ensure_local_tenancy(db)
    board = Board(
        board_id=board_id,
        workspace_id=LOCAL_WORKSPACE_ID,
        name="HaltFreeze Board",
        kind="standard",
        created_by=LOCAL_USER_ID,
    )
    db.add(board)
    await db.flush()
    for idx, (col_id, stage, name) in enumerate(
        [
            (f"{board_id}_backlog", Stage.BACKLOG.value, "Backlog"),
            (f"{board_id}_in_prog", Stage.IN_PROGRESS.value, "In Progress"),
            (f"{board_id}_human_rev", Stage.HUMAN_REVIEW.value, "Human Review"),
            (f"{board_id}_done", Stage.DONE.value, "Done"),
        ]
    ):
        db.add(
            Column(
                column_id=col_id,
                board_id=board_id,
                workspace_id=LOCAL_WORKSPACE_ID,
                name=name,
                stage=stage,
                position=idx,
            )
        )
    await db.commit()
    return board_id


@pytest.mark.asyncio
async def test_agent_can_set_halt_but_only_human_can_clear(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    # Agent sets halt
    resp = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/halt",
        headers=_agent_headers(),
        json={"reason": "loop detected anomaly"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["halted"] is True
    assert body["frozen"] is False
    assert body["halt_reason"] == "loop detected anomaly"

    # Read API reflects halt
    ws_read = await client.get(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}",
        headers=_user_headers(),
    )
    assert ws_read.status_code == 200
    assert ws_read.json()["halted"] is True
    assert ws_read.json()["halt_reason"] == "loop detected anomaly"

    state_read = await client.get(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/state",
        headers=_agent_headers(),
    )
    assert state_read.status_code == 200
    assert state_read.json()["halted"] is True

    # Agent cannot clear halt
    agent_clear = await client.delete(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/halt",
        headers=_agent_headers(),
    )
    assert agent_clear.status_code == 403
    assert agent_clear.json()["error"]["code"] == "ROLE_FORBIDDEN"

    # Human can clear halt
    human_clear = await client.delete(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/halt",
        headers=_user_headers(),
    )
    assert human_clear.status_code == 200
    assert human_clear.json()["halted"] is False
    assert human_clear.json()["halt_reason"] is None


@pytest.mark.asyncio
async def test_halt_blocks_next_and_claim_but_allows_heartbeat_and_human_verdict(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    board_id = await _setup_board_with_columns(test_db, "brd_hf_halt")

    backlog_ticket = Ticket(
        ticket_id="tkt_hf_backlog",
        board_id=board_id,
        column_id=f"{board_id}_backlog",
        workspace_id=LOCAL_WORKSPACE_ID,
        title="Queued work",
        rank="0001",
        created_by=LOCAL_USER_ID,
    )
    review_ticket = Ticket(
        ticket_id="tkt_hf_hrev",
        board_id=board_id,
        column_id=f"{board_id}_human_rev",
        workspace_id=LOCAL_WORKSPACE_ID,
        title="Needs human verdict",
        rank="0002",
        created_by=LOCAL_USER_ID,
        submitted_by=LOCAL_AGENT_WORKER_ID,
    )
    in_prog_ticket = Ticket(
        ticket_id="tkt_hf_inprog",
        board_id=board_id,
        column_id=f"{board_id}_backlog",
        workspace_id=LOCAL_WORKSPACE_ID,
        title="Claimed before halt",
        rank="0003",
        created_by=LOCAL_USER_ID,
    )
    test_db.add_all([backlog_ticket, review_ticket, in_prog_ticket])
    await test_db.commit()

    # Worker claims in_prog_ticket before halt
    claim_before = await client.post(
        "/api/v1/tickets/tkt_hf_inprog/claim",
        headers=_agent_headers(),
    )
    assert claim_before.status_code == 200
    claim_token = claim_before.json()["claim_token"]

    # Halt workspace
    halt_resp = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/halt",
        headers=_agent_headers(),
        json={"reason": "emergency stop"},
    )
    assert halt_resp.status_code == 200

    # next returns distinct WORKSPACE_HALTED signal
    next_resp = await client.get(
        f"/api/v1/boards/{board_id}/next",
        headers=_agent_headers(),
    )
    assert next_resp.status_code == 423
    assert next_resp.json()["error"]["code"] == "WORKSPACE_HALTED"

    # new claim returns distinct WORKSPACE_HALTED signal
    claim_resp = await client.post(
        "/api/v1/tickets/tkt_hf_backlog/claim",
        headers=_agent_headers(),
    )
    assert claim_resp.status_code == 423
    assert claim_resp.json()["error"]["code"] == "WORKSPACE_HALTED"

    # Already-claimed worker can still heartbeat (T6)
    hb_resp = await client.post(
        "/api/v1/tickets/tkt_hf_inprog/heartbeat",
        headers=_agent_headers(),
        json={"claim_token": claim_token},
    )
    assert hb_resp.status_code == 200

    # Human can still verdict
    verdict_resp = await client.post(
        "/api/v1/tickets/tkt_hf_hrev/verdict",
        headers=_user_headers(),
        json={"verdict": {"decision": "approve", "reason": "looks good"}},
    )
    assert verdict_resp.status_code == 200
    assert verdict_resp.json()["stage"] == Stage.DONE.value

    # After human clears halt, next returns backlog work again (G1.1)
    clear_resp = await client.delete(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/halt",
        headers=_user_headers(),
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["halted"] is False

    next_resumed = await client.get(
        f"/api/v1/boards/{board_id}/next",
        headers=_agent_headers(),
    )
    assert next_resumed.status_code == 200
    assert next_resumed.json()["ticket_id"] == "tkt_hf_backlog"


@pytest.mark.asyncio
async def test_freeze_permissions_and_distinct_signals(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    board_id = await _setup_board_with_columns(test_db, "brd_hf_freeze")

    # Ordinary no-work returns 204
    empty_next = await client.get(
        f"/api/v1/boards/{board_id}/next",
        headers=_agent_headers(),
    )
    assert empty_next.status_code == 204

    ticket = Ticket(
        ticket_id="tkt_hf_freeze_1",
        board_id=board_id,
        column_id=f"{board_id}_backlog",
        workspace_id=LOCAL_WORKSPACE_ID,
        title="Wait during freeze",
        rank="0001",
        created_by=LOCAL_USER_ID,
    )
    test_db.add(ticket)
    await test_db.commit()

    # Non-PM agent cannot set freeze
    agent_freeze = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/freeze",
        headers=_agent_headers(),
        json={"reason": "maintenance"},
    )
    assert agent_freeze.status_code == 403
    assert agent_freeze.json()["error"]["code"] == "ROLE_FORBIDDEN"

    # Human sets freeze
    human_freeze = await client.post(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/freeze",
        headers=_user_headers(),
        json={"reason": "planned pause"},
    )
    assert human_freeze.status_code == 200
    assert human_freeze.json()["frozen"] is True
    assert human_freeze.json()["halted"] is False
    assert human_freeze.json()["freeze_reason"] == "planned pause"

    # next and claim return distinct WORKSPACE_FROZEN signal
    next_frozen = await client.get(
        f"/api/v1/boards/{board_id}/next",
        headers=_agent_headers(),
    )
    assert next_frozen.status_code == 423
    assert next_frozen.json()["error"]["code"] == "WORKSPACE_FROZEN"

    claim_frozen = await client.post(
        "/api/v1/tickets/tkt_hf_freeze_1/claim",
        headers=_agent_headers(),
    )
    assert claim_frozen.status_code == 423
    assert claim_frozen.json()["error"]["code"] == "WORKSPACE_FROZEN"

    # Non-PM agent cannot clear freeze
    agent_unfreeze = await client.delete(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/freeze",
        headers=_agent_headers(),
    )
    assert agent_unfreeze.status_code == 403
    assert agent_unfreeze.json()["error"]["code"] == "ROLE_FORBIDDEN"

    # Human clears freeze -> next returns work again
    human_unfreeze = await client.delete(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}/freeze",
        headers=_user_headers(),
    )
    assert human_unfreeze.status_code == 200
    assert human_unfreeze.json()["frozen"] is False

    next_resumed = await client.get(
        f"/api/v1/boards/{board_id}/next",
        headers=_agent_headers(),
    )
    assert next_resumed.status_code == 200
    assert next_resumed.json()["ticket_id"] == "tkt_hf_freeze_1"


@pytest.mark.asyncio
async def test_ticket_attention_does_not_halt_or_freeze_workspace(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    board_id = await _setup_board_with_columns(test_db, "brd_hf_attn")
    test_db.add_all(
        [
            Ticket(
                ticket_id="tkt_hf_attn",
                board_id=board_id,
                column_id=f"{board_id}_backlog",
                workspace_id=LOCAL_WORKSPACE_ID,
                title="Attention ticket",
                rank="0001",
                created_by=LOCAL_USER_ID,
            ),
            Ticket(
                ticket_id="tkt_hf_ready",
                board_id=board_id,
                column_id=f"{board_id}_backlog",
                workspace_id=LOCAL_WORKSPACE_ID,
                title="Ready ticket",
                rank="0002",
                created_by=LOCAL_USER_ID,
            ),
        ]
    )
    await test_db.commit()

    # Flag attention on tkt_hf_attn (#200)
    attn_resp = await client.post(
        "/api/v1/tickets/tkt_hf_attn/attention",
        headers=_agent_headers(),
        json={"reason": "needs human input"},
    )
    assert attn_resp.status_code == 200
    assert attn_resp.json()["attention_flag"] is True

    # Workspace halt and freeze remain False
    ws_read = await client.get(
        f"/api/v1/workspaces/{LOCAL_WORKSPACE_ID}",
        headers=_user_headers(),
    )
    assert ws_read.status_code == 200
    assert ws_read.json()["halted"] is False
    assert ws_read.json()["frozen"] is False

    # next still returns work (not halted/frozen)
    next_resp = await client.get(
        f"/api/v1/boards/{board_id}/next",
        headers=_agent_headers(),
    )
    assert next_resp.status_code == 200
    assert next_resp.json()["ticket_id"] == "tkt_hf_attn"
