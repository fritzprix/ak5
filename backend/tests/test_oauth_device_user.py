"""Issue #25: OAuth device grant — user/agent/refresh/revoke (identity.md §3.3 / §4)."""

from __future__ import annotations

import re

import pytest
from ak5.server.auth.device_grant import clear_user_code_rate_limits_for_tests
from ak5.server.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from ak5.server.auth.tokens import verify_access_token
from ak5.server.seed import LOCAL_USER_ID
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
USER_CODE_RE = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    clear_user_code_rate_limits_for_tests()
    yield
    clear_user_code_rate_limits_for_tests()


async def _login(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert login.status_code == 200
    assert SESSION_COOKIE in login.cookies
    return login.json()["csrf"]


@pytest.mark.asyncio
async def test_user_device_grant_pending_approve_token(
    client: AsyncClient,
    anon_client: AsyncClient,
    test_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    start = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "user"},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    assert USER_CODE_RE.match(body["user_code"])
    assert body["interval"] >= 1
    assert "/device" in body["verification_uri"]
    device_code = body["device_code"]
    user_code = body["user_code"]

    pending = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"

    # Lookup requires web session (not anonymous).
    anon_info = await anon_client.get("/oauth/device/request", params={"code": user_code})
    assert anon_info.status_code == 401

    csrf = await _login(client, monkeypatch)
    info = await client.get("/oauth/device/request", params={"code": user_code})
    assert info.status_code == 200
    assert info.json()["status"] == "pending"
    assert info.json()["principal"] == "user"
    assert "client_id" not in info.json()

    unauth = await anon_client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
    )
    assert unauth.status_code == 401

    # Cookie CSRF alone must not authorize approve.
    cookie_only = await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
    )
    assert cookie_only.status_code == 403
    assert CSRF_COOKIE in client.cookies

    approve = await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
        headers={CSRF_HEADER: csrf},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "approved"

    token = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    assert token.status_code == 200, token.text
    payload = token.json()
    assert payload["token_type"] == "Bearer"
    assert payload["expires_in"] == 900
    assert payload["refresh_token"]
    access = payload["access_token"]
    refresh = payload["refresh_token"]

    principal = await verify_access_token(access, test_db)
    assert principal.principal_type == "user"
    assert principal.principal_id == LOCAL_USER_ID
    assert principal.workspace_id == "ws_local"

    again = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"

    # Refresh rotation
    refreshed = await anon_client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "refresh_token": refresh},
    )
    assert refreshed.status_code == 200, refreshed.text
    new_refresh = refreshed.json()["refresh_token"]
    assert new_refresh != refresh
    await verify_access_token(refreshed.json()["access_token"], test_db)

    # Reuse of old refresh → invalid_grant (and family revoked)
    reuse = await anon_client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "refresh_token": refresh},
    )
    assert reuse.status_code == 400
    assert reuse.json()["error"] == "invalid_grant"
    dead = await anon_client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "refresh_token": new_refresh},
    )
    assert dead.status_code == 400


@pytest.mark.asyncio
async def test_user_device_grant_denied(
    client: AsyncClient,
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    start = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "user"},
    )
    device_code = start.json()["device_code"]
    user_code = start.json()["user_code"]

    csrf = await _login(client, monkeypatch)
    deny = await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": False},
        headers={CSRF_HEADER: csrf},
    )
    assert deny.status_code == 200
    assert deny.json()["status"] == "denied"

    poll = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    assert poll.status_code == 400
    assert poll.json()["error"] == "access_denied"


@pytest.mark.asyncio
async def test_agent_device_grant_and_revoke(
    client: AsyncClient,
    anon_client: AsyncClient,
    test_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    start = await anon_client.post(
        "/oauth/device_authorization",
        json={
            "client_id": "ak5-cli",
            "principal": "agent",
            "name": "slice-worker",
            "workflow_role": "worker",
            "capabilities": ["python"],
        },
    )
    assert start.status_code == 200, start.text
    device_code = start.json()["device_code"]
    user_code = start.json()["user_code"]

    csrf = await _login(client, monkeypatch)
    approve = await client.post(
        "/oauth/device/approve",
        json={
            "user_code": user_code,
            "approved": True,
            "workflow_role": "worker",
            "board_scope": [],
        },
        headers={CSRF_HEADER: csrf},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["principal"] == "agent"

    token = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    assert token.status_code == 200, token.text
    opaque = token.json()["access_token"]
    assert opaque.startswith("ak5_agt_")
    agent_id = token.json()["agent_id"]

    principal = await verify_access_token(opaque, test_db)
    assert principal.principal_type == "agent"
    assert principal.principal_id == agent_id

    revoked = await anon_client.post("/oauth/revoke", json={"token": opaque})
    assert revoked.status_code == 200

    with pytest.raises(Exception):
        await verify_access_token(opaque, test_db)


@pytest.mark.asyncio
async def test_approve_rejects_bad_csrf_header(
    client: AsyncClient,
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    start = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "user"},
    )
    user_code = start.json()["user_code"]
    await _login(client, monkeypatch)
    bad = await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
        headers={CSRF_HEADER: "not-the-real-csrf"},
    )
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_user_code_lookup_rate_limit(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    csrf = await _login(client, monkeypatch)
    _ = csrf
    for _ in range(20):
        resp = await client.get("/oauth/device/request", params={"code": "ZZZZ-ZZZZ"})
        assert resp.status_code == 404
    limited = await client.get("/oauth/device/request", params={"code": "AAAA-AAAA"})
    assert limited.status_code == 400
    assert limited.json()["error"] == "slow_down"
