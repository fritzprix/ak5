"""MCP tools — v2 verb mapping and claim → submit → verdict regression tests."""

from __future__ import annotations

import json

import ak5.mcp.tools as mcp_tools
import pytest
from ak5.contracts.errors import ErrorCode
from ak5.contracts.payloads import Deliverable, Verdict
from ak5.mcp.client import AK5Client
from ak5.mcp.tools import (
    V2_CLI_TOOL_MAP,
    V2_TOOL_NAMES,
    ak5_archive_ticket,
    ak5_delegate_subtask,
    ak5_get_ticket_context,
    ak5_list_available_agents,
    ak5_list_boards,
    ak5_next,
    ak5_report_block,
    ak5_search_tickets,
    ak5_ticket_claim,
    ak5_ticket_create,
    ak5_ticket_submit,
    ak5_ticket_verdict,
    ak5_update_ticket_status,
    server,
)
from ak5.models.actor import Actor
from ak5.routers.auth import create_access_token
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _parse(content_list: list) -> dict | list | None:
    assert len(content_list) == 1
    return json.loads(content_list[0].text)


async def _seed_workflow_agents(db: AsyncSession) -> None:
    actors = [
        Actor(
            actor_id="agent_worker",
            actor_type="agent",
            name="Worker Bot",
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
            actor_id="agent_orchestrator",
            actor_type="agent",
            name="Orchestrator",
            role="triage",
            capabilities=json.dumps(["orchestration", "delegation", "task-execution"]),
            status="idle",
        ),
    ]
    for a in actors:
        if await db.get(Actor, a.actor_id) is None:
            db.add(a)
    await db.commit()


def _bind_mcp(anon_client: AsyncClient, monkeypatch, actor_id: str, actor_type: str = "agent") -> AK5Client:
    client = AK5Client(base_url="http://test/api/v1", actor_id=actor_id)
    client._http = anon_client
    client.use_bearer(create_access_token(actor_id, actor_type), actor_id=actor_id)
    monkeypatch.setattr(mcp_tools, "_client", client)
    return client


# ---------------------------------------------------------------------------
# Completion criteria: tool ↔ CLI verb correspondence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_v2_tools_match_cli_verbs():
    """Every cli.md §4.3/§4.4 verb listed in the issue maps to a registered MCP tool."""
    expected_cli = {
        "next",
        "ticket claim",
        "ticket heartbeat",
        "ticket release",
        "ticket submit",
        "ticket bounce",
        "ticket route",
        "ticket verdict",
        "ticket postmortem",
        "ticket create",
        "ticket block",
        "ticket unblock",
        "ticket reopen",
        "ticket reroute",
        "ticket history",
    }
    assert set(V2_CLI_TOOL_MAP) == expected_cli
    assert len(V2_CLI_TOOL_MAP) == len(V2_TOOL_NAMES)

    registered = {t.name for t in await server.list_tools()}
    missing = V2_TOOL_NAMES - registered
    assert not missing, f"MCP server missing v2 tools: {sorted(missing)}"

    for cli_verb, tool_name in V2_CLI_TOOL_MAP.items():
        assert tool_name in registered, f"{cli_verb!r} → {tool_name} not registered"


@pytest.mark.asyncio
async def test_mcp_payload_tools_use_contracts_schema():
    """submit / route / verdict / postmortem input schemas embed contracts models."""
    by_name = {t.name: t for t in await server.list_tools()}

    submit_schema = by_name["ak5_ticket_submit"].input_schema
    assert "Deliverable" in json.dumps(submit_schema)

    route_schema = by_name["ak5_ticket_route"].input_schema
    assert "RoutePlan" in json.dumps(route_schema)

    verdict_schema = by_name["ak5_ticket_verdict"].input_schema
    assert "Verdict" in json.dumps(verdict_schema)

    pm_schema = by_name["ak5_ticket_postmortem"].input_schema
    assert "Postmortem" in json.dumps(pm_schema)

    block_schema = by_name["ak5_ticket_block"].input_schema
    schema_text = json.dumps(block_schema)
    assert "needs_human" in schema_text or "BlockedKind" in schema_text


@pytest.mark.asyncio
async def test_mcp_deprecated_tools_still_registered():
    registered = {t.name for t in await server.list_tools()}
    assert "ak5_update_ticket_status" in registered
    assert "ak5_report_block" in registered
    tools = {t.name: t for t in await server.list_tools()}
    assert "DEPRECATED" in (tools["ak5_update_ticket_status"].description or "")
    assert "DEPRECATED" in (tools["ak5_report_block"].description or "")


# ---------------------------------------------------------------------------
# Completion criteria: claim → submit → verdict via MCP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_claim_submit_verdict_flow(
    anon_client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    await _seed_workflow_agents(test_db)
    human = auth_headers("user_pm", "human")

    created = await anon_client.post(
        "/api/v1/tickets",
        json=board_ticket("MCP flow feature", acceptance_criteria=["users can log in"]),
        headers=human,
    )
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]

    # next (worker) — should surface the backlog ticket
    _bind_mcp(anon_client, monkeypatch, "agent_worker")
    next_res = await ak5_next(board_id="proj-core-engine")
    next_body = _parse(next_res)
    assert next_body["ticket"] is not None
    assert next_body["ticket"]["ticket_id"] == tid

    # claim
    claim_res = await ak5_ticket_claim(ticket_id=tid, capabilities=["python"])
    claim_body = _parse(claim_res)
    assert "error" not in claim_body
    assert claim_body["claim_token"]
    assert claim_body["ticket"]["stage"] == "in_progress"
    wtoken = claim_body["claim_token"]

    # submit
    deliverable = Deliverable.model_validate(
        {
            "summary": "Auth done",
            "artifacts": [{"kind": "commit", "ref": "abc123"}],
            "criteria_self_check": [
                {"criterion": "users can log in", "pass": True, "note": "ok"}
            ],
        }
    )
    submit_res = await ak5_ticket_submit(
        ticket_id=tid, claim_token=wtoken, deliverable=deliverable
    )
    submit_body = _parse(submit_res)
    assert "error" not in submit_body
    assert submit_body["stage"] == "agent_review"

    # gatekeeper claim + verdict approve
    _bind_mcp(anon_client, monkeypatch, "agent_gk")
    gclaim = _parse(await ak5_ticket_claim(ticket_id=tid))
    assert "error" not in gclaim
    gtoken = gclaim["claim_token"]

    verdict = Verdict.model_validate(
        {
            "decision": "approve",
            "findings": [{"criterion": "users can log in", "pass": True, "note": "lgtm"}],
        }
    )
    verdict_res = await ak5_ticket_verdict(
        ticket_id=tid, claim_token=gtoken, verdict=verdict
    )
    verdict_body = _parse(verdict_res)
    assert "error" not in verdict_body
    assert verdict_body["stage"] == "human_review"


@pytest.mark.asyncio
async def test_mcp_error_code_exposed_on_invalid_transition(
    anon_client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    await _seed_workflow_agents(test_db)
    human = auth_headers("user_pm", "human")
    tid = (
        await anon_client.post(
            "/api/v1/tickets",
            json=board_ticket("Cannot submit without claim"),
            headers=human,
        )
    ).json()["ticket_id"]

    _bind_mcp(anon_client, monkeypatch, "agent_worker")
    deliverable = Deliverable.model_validate(
        {"summary": "nope", "artifacts": [], "criteria_self_check": []}
    )
    res = await ak5_ticket_submit(
        ticket_id=tid, claim_token="bogus-token", deliverable=deliverable
    )
    body = _parse(res)
    assert "error" in body
    assert body["error"]["code"] == ErrorCode.STALE_CLAIM.value or body["error"][
        "code"
    ] in {c.value for c in ErrorCode}


@pytest.mark.asyncio
async def test_mcp_create_and_block_via_v2_tools(
    anon_client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    await _seed_workflow_agents(test_db)
    _bind_mcp(anon_client, monkeypatch, "user_pm", actor_type="human")

    create_res = await ak5_ticket_create(
        title="Inbox request",
        original_request="Please triage this",
    )
    created = _parse(create_res)
    assert created["board_id"] == "inbox"
    assert created["stage"] == "unclassified"
    tid = created["ticket_id"]

    # human can block unclassified
    from ak5.mcp.tools import ak5_ticket_block

    block_res = await ak5_ticket_block(
        ticket_id=tid,
        kind="needs_human",
        reason="Need clarification on scope",
    )
    blocked = _parse(block_res)
    assert "error" not in blocked
    assert blocked.get("blocked_kind") == "needs_human" or blocked.get("status") == "blocked"


# ---------------------------------------------------------------------------
# Legacy discovery flow (compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tools_flow(anon_client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch):
    await _seed_workflow_agents(test_db)
    # Use unauthenticated ASGI client so MCP must attach its own Bearer JWT on reads.
    test_mcp_client = AK5Client(base_url="http://test/api/v1", actor_id="agent_orchestrator")
    test_mcp_client._http = anon_client
    test_mcp_client.use_bearer(
        create_access_token("agent_orchestrator", "agent"),
        actor_id="agent_orchestrator",
    )
    monkeypatch.setattr(mcp_tools, "_client", test_mcp_client)

    # 0. Test ak5_list_boards
    boards_res = await ak5_list_boards()
    assert len(boards_res) == 1
    assert "proj-core-engine" in boards_res[0].text

    # 1. Test ak5_list_available_agents
    agents_res = await ak5_list_available_agents(capability="image-resize")
    assert len(agents_res) == 1
    assert "agent_image_worker" in agents_res[0].text

    # Create a parent ticket first via API
    headers = auth_headers("user_pm", "human")
    t_resp = await anon_client.post(
        "/api/v1/tickets",
        json={
            "title": "Master Feature",
            "board_id": "proj-core-engine",
            "acceptance_criteria": ["done"],
            "spec": "Master feature spec",
        },
        headers=headers,
    )
    parent_id = t_resp.json()["ticket_id"]

    # 2. Test ak5_delegate_subtask
    del_res = await ak5_delegate_subtask(
        parent_ticket_id=parent_id,
        target_agent_id="agent_image_worker",
        title="Worker Subtask from MCP",
        description="Detailed MCP description",
        priority="high",
    )
    assert len(del_res) == 1
    assert "successfully created and delegated" in del_res[0].text
    assert "agent_image_worker" in del_res[0].text

    # 3. Test ak5_get_ticket_context
    ctx_res = await ak5_get_ticket_context(parent_id)
    assert len(ctx_res) == 1
    assert f"Ticket Context: {parent_id}" in ctx_res[0].text
    assert "Worker Subtask from MCP" in ctx_res[0].text

    # 4. Test ak5_update_ticket_status (same-stage column only; stage jumps need
    # workflow transition commands — workflow.md §8)
    upd_res = await ak5_update_ticket_status(
        ticket_id=parent_id,
        column_name="Backlog",
        status_note="Orchestrator started coordinating subtasks",
        execution_context={"branch": "feat/mcp-flow", "ci_status": "passing"},
    )
    assert len(upd_res) == 1
    assert f"Ticket '{parent_id}' updated" in upd_res[0].text

    # 5. Test ak5_report_block (deprecated → v2 block)
    block_res = await ak5_report_block(
        ticket_id=parent_id,
        blocking_reason="Missing API keys for cloud WebP optimizer",
        required_actor_id="user_pm",
    )
    assert "is now BLOCKED" in block_res[0].text
    assert "@user_pm" in block_res[0].text

    # 6. Test ak5_search_tickets (active only by default)
    search_res = await ak5_search_tickets(query="Master Feature")
    assert len(search_res) == 1
    assert parent_id in search_res[0].text

    # 7. Test ak5_archive_ticket
    arc_res = await ak5_archive_ticket(ticket_id=parent_id)
    assert len(arc_res) == 1
    assert f"Ticket '{parent_id}' successfully archived" in arc_res[0].text

    # Search active should no longer show archived ticket
    search_active_res = await ak5_search_tickets(query="Master Feature", is_archived=False)
    assert "No tickets found" in search_active_res[0].text

    # Search archived should show it
    search_arc_res = await ak5_search_tickets(query="Master Feature", is_archived=True)
    assert parent_id in search_arc_res[0].text
    assert "[ARCHIVED]" in search_arc_res[0].text

    # 8. Test ak5_archive_ticket with unarchive=True
    unarc_res = await ak5_archive_ticket(ticket_id=parent_id, unarchive=True)
    assert "successfully unarchived" in unarc_res[0].text
