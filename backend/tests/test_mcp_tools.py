import ak5.mcp.tools as mcp_tools
import pytest
from ak5.mcp.client import AK5Client
from ak5.mcp.tools import (
    ak5_delegate_subtask,
    ak5_get_ticket_context,
    ak5_list_available_agents,
    ak5_report_block,
    ak5_update_ticket_status,
)
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_mcp_tools_flow(client: AsyncClient, auth_headers, monkeypatch):
    # Set up AK5Client to talk to test ASGI client
    test_mcp_client = AK5Client(base_url="http://test/api/v1", actor_id="agent_orchestrator")
    # Replace internal httpx client with test client
    test_mcp_client._http = client
    monkeypatch.setattr(mcp_tools, "_client", test_mcp_client)

    # 1. Test ak5_list_available_agents
    agents_res = await ak5_list_available_agents(capability="image-resize")
    assert len(agents_res) == 1
    assert "agent_image_worker" in agents_res[0].text

    # Create a parent ticket first via API
    headers = auth_headers("user_pm", "human")
    t_resp = await client.post(
        "/api/v1/tickets",
        json={"title": "Master Feature", "board_id": "proj-core-engine", "column_id": "col_todo"},
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

    # 4. Test ak5_update_ticket_status
    upd_res = await ak5_update_ticket_status(
        ticket_id=parent_id,
        column_name="In Progress",
        status_note="Orchestrator started coordinating subtasks",
        execution_context={"branch": "feat/mcp-flow", "ci_status": "passing"},
    )
    assert len(upd_res) == 1
    assert f"Ticket '{parent_id}' updated" in upd_res[0].text
    assert "in_progress" in upd_res[0].text

    # 5. Test ak5_report_block
    block_res = await ak5_report_block(
        ticket_id=parent_id,
        blocking_reason="Missing API keys for cloud WebP optimizer",
        required_actor_id="user_pm",
    )
    assert len(block_res) == 1
    assert "is now BLOCKED" in block_res[0].text
    assert "@user_pm" in block_res[0].text
