import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_board_members_default_seed(client: AsyncClient):
    res = await client.get("/api/v1/boards/proj-core-engine/members")
    assert res.status_code == 200, res.text
    members = res.json()
    member_ids = {m["actor_id"] for m in members}
    assert "user_pm" in member_ids
    assert "agent_image_worker" in member_ids


@pytest.mark.asyncio
async def test_add_and_remove_board_member(client: AsyncClient, auth_headers):
    # 1. Create a new actor
    act_res = await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "member_test_agent",
            "actor_type": "agent",
            "name": "Member Test Agent",
            "role": "Tester",
            "capabilities": ["test"],
        },
    )
    assert act_res.status_code == 200

    # 2. Add member to proj-core-engine (as board creator/admin user_pm)
    add_res = await client.post(
        "/api/v1/boards/proj-core-engine/members",
        json={"actor_id": "member_test_agent", "role": "tester"},
        headers=auth_headers("user_pm", "human"),
    )
    assert add_res.status_code == 200, add_res.text
    assert add_res.json()["actor_id"] == "member_test_agent"

    # 3. Verify in member list
    list_res = await client.get("/api/v1/boards/proj-core-engine/members")
    assert list_res.status_code == 200
    member_ids = [m["actor_id"] for m in list_res.json()]
    assert "member_test_agent" in member_ids

    # 4. Remove member
    del_res = await client.delete(
        "/api/v1/boards/proj-core-engine/members/member_test_agent",
        headers=auth_headers("user_pm", "human"),
    )
    assert del_res.status_code == 200

    # 5. Verify no longer in list
    list_res2 = await client.get("/api/v1/boards/proj-core-engine/members")
    member_ids2 = [m["actor_id"] for m in list_res2.json()]
    assert "member_test_agent" not in member_ids2


@pytest.mark.asyncio
async def test_unauthorized_member_add_blocked(client: AsyncClient, auth_headers):
    # Register an external non-admin agent
    await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "stranger_agent",
            "actor_type": "agent",
            "name": "Stranger Agent",
            "role": "Guest",
        },
    )

    # stranger_agent attempts to add a member to proj-core-engine (must fail with 403)
    add_res = await client.post(
        "/api/v1/boards/proj-core-engine/members",
        json={"actor_id": "some_other_agent", "role": "admin"},
        headers=auth_headers("stranger_agent", "agent"),
    )
    assert add_res.status_code == 403
    assert "Board administrator privileges required" in add_res.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_remove_board_creator(client: AsyncClient, auth_headers):
    # Try to remove user_pm (the creator of proj-core-engine)
    del_res = await client.delete(
        "/api/v1/boards/proj-core-engine/members/user_pm",
        headers=auth_headers("user_pm", "human"),
    )
    assert del_res.status_code == 400
    assert "Cannot remove the creator" in del_res.json()["detail"]


@pytest.mark.asyncio
async def test_create_board_auto_adds_creator(client: AsyncClient, auth_headers):
    # Create new board
    new_board_res = await client.post(
        "/api/v1/boards",
        json={"name": "Auto Creator Board"},
        headers=auth_headers("user_pm", "human"),
    )
    assert new_board_res.status_code == 200
    b_data = new_board_res.json()
    board_id = b_data["board_id"]

    # Check members
    mem_res = await client.get(f"/api/v1/boards/{board_id}/members")
    assert mem_res.status_code == 200
    members = mem_res.json()
    assert len(members) >= 1
    assert any(m["actor_id"] == "user_pm" for m in members)
