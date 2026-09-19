import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_auth_identify(client: AsyncClient):
    payload = {
        "actor_id": "agent_test_worker",
        "actor_type": "agent",
        "name": "Test Worker",
        "role": "Testing Specialist",
        "description": "Handles automated test suites and mock generation",
        "capabilities": ["pytest", "mocking", "e2e"],
    }
    resp = await client.post("/api/v1/auth/identify", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["actor"]["actor_id"] == "agent_test_worker"
    assert "pytest" in data["actor"]["capabilities"]


@pytest.mark.asyncio
async def test_agent_discovery(client: AsyncClient):
    # Discovery by capability
    resp = await client.get("/api/v1/actors/discovery?capability=image-resize")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) >= 1
    assert any(a["actor_id"] == "agent_image_worker" for a in agents)

    # Discovery by semantic query
    resp2 = await client.get("/api/v1/actors/discovery?query=webp")
    assert resp2.status_code == 200
    agents2 = resp2.json()
    assert len(agents2) >= 1
    assert agents2[0]["actor_id"] == "agent_image_worker"

    # Discovery with non-matching capability
    resp3 = await client.get("/api/v1/actors/discovery?capability=kubernetes-operator")
    assert resp3.status_code == 200
    agents3 = resp3.json()
    assert len(agents3) == 0
