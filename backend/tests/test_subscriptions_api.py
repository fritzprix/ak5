import pytest
from ak5.cli.main import cli
from click.testing import CliRunner
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_subscriptions_api_crud(client: AsyncClient, anon_client: AsyncClient, auth_headers):
    create_payload = {
        "filter": {
            "board_id": "proj-core-engine",
            "events": ["TICKET_CREATED", "TICKET_MOVED"],
            "for_actor": "agent-worker",
        }
    }

    # 0. Unauthenticated access must fail with 401
    unauth_res = await anon_client.post("/api/v1/subscriptions", json=create_payload)
    assert unauth_res.status_code == 401

    # Agents may register filter-only subscriptions
    agent_headers = auth_headers("agent_image_worker", "agent")
    agent_ok = await client.post("/api/v1/subscriptions", json=create_payload, headers=agent_headers)
    assert agent_ok.status_code == 201, agent_ok.text
    agent_sub_id = agent_ok.json()["subscription_id"]
    await client.delete(f"/api/v1/subscriptions/{agent_sub_id}", headers=agent_headers)

    headers = auth_headers("user_pm", "human")

    # 1. Create subscription
    res = await client.post("/api/v1/subscriptions", json=create_payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()
    sub_id = data["subscription_id"]
    assert sub_id.startswith("sub-")
    assert data["filter"]["board_id"] == "proj-core-engine"
    assert "exec_command" not in data
    assert "TICKET_CREATED" in data["filter"]["events"]
    assert data["owner"] == "user_pm"

    # 2. List subscriptions
    list_res = await client.get("/api/v1/subscriptions", headers=headers)
    assert list_res.status_code == 200
    items = list_res.json()
    assert any(s["subscription_id"] == sub_id for s in items)

    # 3. Filter by board_id
    filtered_res = await client.get("/api/v1/subscriptions?board_id=proj-core-engine", headers=headers)
    assert filtered_res.status_code == 200
    assert any(s["subscription_id"] == sub_id for s in filtered_res.json())

    # 4. Get by ID
    get_res = await client.get(f"/api/v1/subscriptions/{sub_id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["subscription_id"] == sub_id

    # 5. AuthZ: another actor cannot view or delete
    other_headers = auth_headers("agent_image_worker", "agent")
    forbidden_get = await client.get(f"/api/v1/subscriptions/{sub_id}", headers=other_headers)
    assert forbidden_get.status_code == 403

    forbidden_del = await client.delete(f"/api/v1/subscriptions/{sub_id}", headers=other_headers)
    assert forbidden_del.status_code == 403

    other_list = await client.get("/api/v1/subscriptions", headers=other_headers)
    assert other_list.status_code == 200
    assert not any(s["subscription_id"] == sub_id for s in other_list.json())

    # 6. Delete by creator
    del_res = await client.delete(f"/api/v1/subscriptions/{sub_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "unsubscribed"

    get_after = await client.get(f"/api/v1/subscriptions/{sub_id}", headers=headers)
    assert get_after.status_code == 404


@pytest.mark.skip(reason="rewritten in #13")
def test_cli_subscribe_dry_run_exits_immediately():
    runner = CliRunner()
    result = runner.invoke(
        cli, ["subscribe", "create", "proj-libragent-dev", "--exec", 'echo "[$AK5_EVENT] $AK5_TICKET_ID"', "--dry-run"]
    )
    assert result.exit_code == 0


@pytest.mark.skip(reason="rewritten in #13")
def test_cli_subscribe_registers_and_returns_immediately(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["subscribe", "create", "proj-libragent-dev", "--exec", "echo 'Hello'"],
    )
    assert result.exit_code == 0


@pytest.mark.skip(reason="rewritten in #13")
def test_cli_subscriptions_list(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "list"])
    assert result.exit_code == 0


@pytest.mark.skip(reason="rewritten in #13")
def test_cli_unsubscribe(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "remove", "sub-001"])
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_actor_patch_role_escalation_blocked(client: AsyncClient, auth_headers):
    """Verify that a non-admin actor cannot elevate their own role to admin or modify other actors."""
    user_headers = auth_headers("agent_image_worker", "agent")

    res_other = await client.patch(
        "/api/v1/actors/user_pm",
        json={"name": "Hacked Name"},
        headers=user_headers,
    )
    assert res_other.status_code == 403
    assert "Access forbidden" in res_other.json()["detail"]

    res_escalate = await client.patch(
        "/api/v1/actors/agent_image_worker",
        json={"role": "admin"},
        headers=user_headers,
    )
    assert res_escalate.status_code == 403
    assert "cannot self-escalate" in res_escalate.json()["detail"]

    res_valid = await client.patch(
        "/api/v1/actors/agent_image_worker",
        json={"status": "busy"},
        headers=user_headers,
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["status"] == "busy"
