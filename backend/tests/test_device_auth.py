import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_device_auth_full_flow(client: AsyncClient, anon_client: AsyncClient, auth_headers):
    # 1. Request device code
    req_payload = {
        "actor_id": "test_agent_devflow",
        "actor_type": "agent",
        "name": "Dev Flow Agent",
        "role": "Integration Tester",
        "capabilities": ["testing", "python"],
        "board_id": "proj-core-engine",
    }
    res = await client.post("/auth/device/code", json=req_payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "device_code" in data
    assert "user_code" in data
    assert data["user_code"].startswith("AK5-")
    assert "verification_uri_complete" in data
    device_code = data["device_code"]
    user_code = data["user_code"]

    # 2. Poll before approval -> authorization_pending (HTTP 400)
    poll_res = await client.post("/auth/device/token", json={"device_code": device_code})
    assert poll_res.status_code == 400
    assert "authorization_pending" in poll_res.json()["detail"]

    # 3. User inspects request details on web UI
    req_view = await client.get(f"/auth/device/request?code={user_code}")
    assert req_view.status_code == 200
    view_data = req_view.json()
    assert view_data["actor_id"] == "test_agent_devflow"
    assert view_data["status"] == "pending"
    assert "proj-core-engine" in view_data["target_board_ids"]

    # 4. Unauthenticated approval must be blocked (HTTP 401)
    unauth_appr = await anon_client.post(
        "/auth/device/approve",
        json={"user_code": user_code, "approved": True, "board_ids": ["proj-core-engine"]},
    )
    assert unauth_appr.status_code == 401

    # 5. Authenticated user approves request with target board
    appr_res = await client.post(
        "/auth/device/approve",
        json={"user_code": user_code, "approved": True, "board_ids": ["proj-core-engine"]},
        headers=auth_headers(),
    )
    assert appr_res.status_code == 200
    assert appr_res.json()["status"] == "approved"

    # 6. Agent polls -> receives access token
    token_res = await client.post("/auth/device/token", json={"device_code": device_code})
    assert token_res.status_code == 200
    token_data = token_res.json()
    assert "access_token" in token_data
    token = token_data["access_token"]
    assert token_data["actor"]["actor_id"] == "test_agent_devflow"

    # 7. Device token is single-use: second poll must be rejected (invalid_grant)
    repoll_res = await client.post("/auth/device/token", json={"device_code": device_code})
    assert repoll_res.status_code == 400
    assert "invalid_grant" in repoll_res.json()["detail"]

    # 8. Verify token works on whoami / actors endpoint
    agent_auth_headers = {"Authorization": f"Bearer {token}"}
    who_res = await client.get("/api/v1/actors/test_agent_devflow", headers=agent_auth_headers)
    assert who_res.status_code == 200
    assert who_res.json()["name"] == "Dev Flow Agent"


@pytest.mark.asyncio
async def test_device_auth_denied(client: AsyncClient, auth_headers):
    # Request code
    res = await client.post(
        "/auth/device/code",
        json={"actor_id": "test_agent_denied", "role": "Tester"},
    )
    assert res.status_code == 200
    data = res.json()
    device_code = data["device_code"]
    user_code = data["user_code"]

    # User denies
    appr_res = await client.post(
        "/auth/device/approve",
        json={"user_code": user_code, "approved": False},
        headers=auth_headers(),
    )
    assert appr_res.status_code == 200
    assert appr_res.json()["status"] == "denied"

    # Agent polls -> access_denied (403)
    poll_res = await client.post("/auth/device/token", json={"device_code": device_code})
    assert poll_res.status_code == 403
    assert "access_denied" in poll_res.json()["detail"]


@pytest.mark.asyncio
async def test_device_auth_existing_actor_rejected(client: AsyncClient):
    # seeded 'user_pm' already exists
    res = await client.post(
        "/auth/device/code",
        json={"actor_id": "user_pm", "role": "PM"},
    )
    # blocked as reserved system actor / existing actor
    assert res.status_code in {403, 409}


@pytest.mark.asyncio
async def test_device_auth_reserved_admin_blocked(client: AsyncClient):
    res = await client.post(
        "/auth/device/code",
        json={"actor_id": "admin", "role": "Administrator"},
    )
    assert res.status_code == 403
