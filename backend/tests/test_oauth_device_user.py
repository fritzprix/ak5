"""Issue #25 slice 1: user principal device grant (identity.md §3.3)."""

from __future__ import annotations

import pytest
from ak5.server.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from ak5.server.auth.tokens import verify_access_token
from ak5.server.seed import LOCAL_USER_ID
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@pytest.mark.asyncio
async def test_user_device_grant_pending_approve_token(
    client: AsyncClient,
    anon_client: AsyncClient,
    test_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")

    start = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "user"},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["user_code"].startswith("AK5-")
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

    info = await anon_client.get("/oauth/device/request", params={"code": user_code})
    assert info.status_code == 200
    assert info.json()["status"] == "pending"
    assert info.json()["principal"] == "user"

    unauth = await anon_client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
    )
    assert unauth.status_code == 401

    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert login.status_code == 200
    csrf = login.json()["csrf"]
    assert SESSION_COOKIE in login.cookies

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


@pytest.mark.asyncio
async def test_user_device_grant_denied(
    client: AsyncClient,
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    start = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "user"},
    )
    device_code = start.json()["device_code"]
    user_code = start.json()["user_code"]

    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    csrf = login.json()["csrf"]
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
async def test_agent_principal_not_yet_implemented(anon_client: AsyncClient):
    resp = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "agent"},
    )
    assert resp.status_code == 501


@pytest.mark.asyncio
async def test_approve_rejects_bad_csrf_header(
    client: AsyncClient,
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    start = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "user"},
    )
    user_code = start.json()["user_code"]
    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert CSRF_COOKIE in login.cookies
    bad = await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
        headers={CSRF_HEADER: "not-the-real-csrf"},
    )
    assert bad.status_code == 403
