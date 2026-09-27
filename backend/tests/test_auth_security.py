import pytest
from ak5.security import (
    IDENTIFY_SECRET_HEADER,
    get_jwt_secret,
    reset_secret_caches,
    resolve_jwt_secret,
)
from ak5.web_auth import COOKIE_NAME, compute_session_hash, reset_rate_limits_for_tests
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _reset_web_rate_limits():
    reset_rate_limits_for_tests()
    yield
    reset_rate_limits_for_tests()


def test_jwt_secret_from_env(monkeypatch):
    monkeypatch.setenv("AK5_JWT_SECRET", "from-env-secret-value")
    reset_secret_caches()
    assert get_jwt_secret() == "from-env-secret-value"


def test_jwt_secret_persists_to_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AK5_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    secret_file = tmp_path / "jwt_secret"
    monkeypatch.setenv("AK5_JWT_SECRET_FILE", str(secret_file))
    reset_secret_caches()

    first = resolve_jwt_secret()
    assert secret_file.is_file()
    assert secret_file.read_text(encoding="utf-8").strip() == first

    reset_secret_caches()
    second = resolve_jwt_secret()
    assert second == first


@pytest.mark.asyncio
async def test_identify_open_without_secret(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "agent_new_worker",
            "actor_type": "agent",
            "name": "New Worker",
            "role": "Worker",
            "capabilities": ["pytest"],
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_identify_requires_secret_when_configured(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AK5_IDENTIFY_SECRET", "shared-agent-secret")
    reset_secret_caches()

    denied = await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "agent_locked",
            "actor_type": "agent",
            "name": "Locked",
            "role": "Worker",
            "capabilities": [],
        },
    )
    assert denied.status_code == 401

    ok = await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "agent_locked",
            "actor_type": "agent",
            "name": "Locked",
            "role": "Worker",
            "capabilities": [],
        },
        headers={IDENTIFY_SECRET_HEADER: "shared-agent-secret"},
    )
    assert ok.status_code == 200
    assert "access_token" in ok.json()


@pytest.mark.asyncio
async def test_identify_body_secret_accepted(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AK5_IDENTIFY_SECRET", "body-secret")
    reset_secret_caches()

    resp = await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "agent_body_secret",
            "actor_type": "agent",
            "name": "Body Secret",
            "role": "Worker",
            "capabilities": [],
            "identify_secret": "body-secret",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_identify_allows_web_auth_cookie(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AK5_IDENTIFY_SECRET", "gate-secret")
    monkeypatch.setenv("AK5_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "web-pass")
    reset_secret_caches()
    reset_rate_limits_for_tests()

    denied = await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "user_pm",
            "actor_type": "human",
            "name": "PM",
            "role": "PM",
            "capabilities": [],
        },
    )
    assert denied.status_code == 401

    client.cookies.set(COOKIE_NAME, compute_session_hash("admin", "web-pass"))
    ok = await client.post(
        "/api/v1/auth/identify",
        json={
            "actor_id": "user_pm",
            "actor_type": "human",
            "name": "PM",
            "role": "PM",
            "capabilities": ["planning"],
        },
    )
    assert ok.status_code == 200
    assert "access_token" in ok.json()


@pytest.mark.asyncio
async def test_actor_patch_ownership_still_enforced(client: AsyncClient, auth_headers):
    """Regression: non-owner cannot PATCH another actor."""
    other = auth_headers("agent_image_worker", "agent")
    resp = await client.patch(
        "/api/v1/actors/user_pm",
        json={"name": "Hijacked"},
        headers=other,
    )
    assert resp.status_code == 403
