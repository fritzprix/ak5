import asyncio

import pytest
from ak5.cli.main import cli
from ak5.services.event_bus import BoardEvent
from ak5.services.subscription_service import subscription_service
from click.testing import CliRunner
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_subscriptions_api_crud(client: AsyncClient, auth_headers):
    # 0. Unauthenticated access must fail with 401 (Prevent RCE)
    create_payload = {
        "board_id": "proj-core-engine",
        "exec_command": "echo triggered >> /tmp/ak5_hook.log",
        "events": ["TICKET_CREATED", "TICKET_MOVED"],
        "for_agent": "agent-worker",
        "debounce_seconds": 1.5,
    }
    unauth_res = await client.post("/api/v1/subscriptions", json=create_payload)
    assert unauth_res.status_code == 401

    headers = auth_headers("user_pm", "human")

    # 1. Create subscription with authenticated actor
    res = await client.post("/api/v1/subscriptions", json=create_payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()
    sub_id = data["subscription_id"]
    assert sub_id.startswith("sub-")
    assert data["board_id"] == "proj-core-engine"
    assert data["exec_command"] == "echo triggered >> /tmp/ak5_hook.log"
    assert "TICKET_CREATED" in data["events"]
    assert data["created_by"] == "user_pm"

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

    # 5. AuthZ: Another non-admin actor (agent_image_worker) cannot view or delete user_pm's hook
    other_headers = auth_headers("agent_image_worker", "agent")
    forbidden_get = await client.get(f"/api/v1/subscriptions/{sub_id}", headers=other_headers)
    assert forbidden_get.status_code == 403
    assert "Access forbidden" in forbidden_get.json()["detail"]

    forbidden_del = await client.delete(f"/api/v1/subscriptions/{sub_id}", headers=other_headers)
    assert forbidden_del.status_code == 403

    # Other actor list should not leak user_pm's hook command
    other_list = await client.get("/api/v1/subscriptions", headers=other_headers)
    assert other_list.status_code == 200
    assert not any(s["subscription_id"] == sub_id for s in other_list.json())

    # 6. Delete subscription by creator
    del_res = await client.delete(f"/api/v1/subscriptions/{sub_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "unsubscribed"

    # Verify deletion
    get_after = await client.get(f"/api/v1/subscriptions/{sub_id}", headers=headers)
    assert get_after.status_code == 404


def test_cli_subscribe_dry_run_exits_immediately():
    """Verify that --dry-run completes immediately with exit code 0 without blocking."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["subscribe", "create", "proj-libragent-dev", "--exec", 'echo "[$AK5_EVENT] $AK5_TICKET_ID"', "--dry-run"]
    )
    assert result.exit_code == 0
    assert "Subscription Dry-Run Validated" in result.output
    assert "Dry-run mode: Subscription was not registered." in result.output
    assert "Hook Command (literal)" in result.output


def test_cli_subscribe_registers_and_returns_immediately(monkeypatch):
    """Verify that ak5 subscribe create sends POST with auth headers and exits immediately."""
    runner = CliRunner()

    class FakeResponse:
        status_code = 201

        def json(self):
            return {
                "subscription_id": "sub-test-123",
                "board_id": "proj-libragent-dev",
                "exec_command": "echo 'Hello'",
            }

    monkeypatch.setattr(
        "ak5.cli.commands.subscribe.get_auth_headers",
        lambda api_url: {"Authorization": "Bearer test-jwt-token"},
    )

    def fake_post(url, json, **kwargs):
        assert "/subscriptions" in url
        assert json["board_id"] == "proj-libragent-dev"
        assert json["exec_command"] == "echo 'Hello'"
        headers = kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer test-jwt-token"
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    result = runner.invoke(
        cli,
        [
            "subscribe",
            "create",
            "proj-libragent-dev",
            "--exec",
            "echo 'Hello'",
        ],
    )
    assert result.exit_code == 0
    assert "Subscription registered successfully" in result.output
    assert "sub-test-123" in result.output


def test_cli_subscriptions_list(monkeypatch):
    runner = CliRunner()

    class FakeResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "subscription_id": "sub-001",
                    "board_id": "proj-core-engine",
                    "events": ["TICKET_MOVED"],
                    "for_agent": "agent-worker",
                    "exec_command": "echo 'Run'",
                }
            ]

    monkeypatch.setattr(
        "ak5.cli.commands.subscribe.get_auth_headers",
        lambda api_url: {"Authorization": "Bearer test-jwt-token"},
    )

    def fake_get(url, **kwargs):
        headers = kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer test-jwt-token"
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    result = runner.invoke(cli, ["subscribe", "list"])
    assert result.exit_code == 0
    assert "sub-001" in result.output
    assert "proj-core-engine" in result.output


def test_cli_unsubscribe(monkeypatch):
    runner = CliRunner()

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"status": "unsubscribed", "subscription_id": "sub-001"}

    monkeypatch.setattr(
        "ak5.cli.commands.subscribe.get_auth_headers",
        lambda api_url: {"Authorization": "Bearer test-jwt-token"},
    )

    def fake_delete(url, **kwargs):
        headers = kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer test-jwt-token"
        return FakeResponse()

    monkeypatch.setattr("httpx.delete", fake_delete)

    result = runner.invoke(cli, ["subscribe", "remove", "sub-001"])
    assert result.exit_code == 0
    assert "Successfully removed subscription hook" in result.output
    assert "sub-001" in result.output


@pytest.mark.asyncio
async def test_subscription_service_execution(tmp_path, client: AsyncClient, auth_headers):
    from conftest import TestAsyncSessionLocal

    orig_factory = subscription_service.session_factory
    subscription_service.session_factory = TestAsyncSessionLocal

    output_file = tmp_path / "service_out.txt"
    cmd = f'echo "[$AK5_EVENT] $AK5_TICKET_ID:$AK5_TITLE" > "{output_file}"'

    headers = auth_headers("user_pm", "human")

    # Create subscription hook
    sub_payload = {
        "board_id": "proj-test",
        "exec_command": cmd,
        "events": ["TICKET_CREATED"],
    }
    res = await client.post("/api/v1/subscriptions", json=sub_payload, headers=headers)
    assert res.status_code == 201
    sub_id = res.json()["subscription_id"]

    try:
        # Dispatch event via subscription_service
        event = BoardEvent(
            event_id=99,
            event_type="TICKET_CREATED",
            data={
                "board_id": "proj-test",
                "ticket": {"ticket_id": "TK-999", "title": "Service Event"},
                "actor_id": "system_user",
            },
            timestamp="2026-09-27T00:00:00Z",
        )
        await subscription_service.handle_board_event(event)

        # Wait briefly for asyncio subprocess background execution
        for _ in range(20):
            if output_file.exists():
                break
            await asyncio.sleep(0.1)

        assert output_file.exists(), "Command was not executed in background"
        content = output_file.read_text().strip()
        assert "[TICKET_CREATED] TK-999:Service Event" in content

    finally:
        subscription_service.session_factory = orig_factory
        await client.delete(f"/api/v1/subscriptions/{sub_id}", headers=headers)


@pytest.mark.asyncio
async def test_actor_patch_role_escalation_blocked(client: AsyncClient, auth_headers):
    """Verify that a non-admin actor cannot elevate their own role to admin or modify other actors."""
    user_headers = auth_headers("agent_image_worker", "agent")

    # 1. Non-admin attempting to modify another actor's profile -> 403 Forbidden
    res_other = await client.patch(
        "/api/v1/actors/user_pm",
        json={"name": "Hacked Name"},
        headers=user_headers,
    )
    assert res_other.status_code == 403
    assert "Access forbidden" in res_other.json()["detail"]

    # 2. Non-admin attempting to self-escalate role to admin -> 403 Forbidden
    res_escalate = await client.patch(
        "/api/v1/actors/agent_image_worker",
        json={"role": "admin"},
        headers=user_headers,
    )
    assert res_escalate.status_code == 403
    assert "cannot self-escalate" in res_escalate.json()["detail"]

    # 3. Non-admin self-updating valid fields (status/name) -> 200 OK
    res_valid = await client.patch(
        "/api/v1/actors/agent_image_worker",
        json={"status": "busy"},
        headers=user_headers,
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["status"] == "busy"
