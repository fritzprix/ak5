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

    # Must reach general API auth (opaque before JWT decode) — 200 before revoke.
    boards = await anon_client.get(
        "/api/v1/boards",
        headers={"Authorization": f"Bearer {opaque}"},
    )
    assert boards.status_code == 200, boards.text

    revoked = await anon_client.post("/oauth/revoke", json={"token": opaque})
    assert revoked.status_code == 200

    boards_after = await anon_client.get(
        "/api/v1/boards",
        headers={"Authorization": f"Bearer {opaque}"},
    )
    assert boards_after.status_code == 401

    with pytest.raises(Exception):
        await verify_access_token(opaque, test_db)


@pytest.mark.asyncio
async def test_agent_board_scope_enforced_on_claim(
    client: AsyncClient,
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers,
):
    """Device-grant board_scope must block out-of-scope ticket writes (§4.3)."""
    from helpers_workflow import board_ticket

    human = auth_headers("user_pm", "human")
    in_scope = await client.post(
        "/api/v1/tickets",
        json=board_ticket("In scope", board_id="proj-core-engine"),
        headers=human,
    )
    assert in_scope.status_code == 201, in_scope.text
    # Second board + ticket outside scope
    other_board = await client.post(
        "/api/v1/boards",
        json={"board_id": "board-other-scope", "name": "Other", "template": "default"},
        headers=human,
    )
    assert other_board.status_code in (200, 201), other_board.text
    out_scope = await client.post(
        "/api/v1/tickets",
        json=board_ticket("Out of scope", board_id="board-other-scope"),
        headers=human,
    )
    assert out_scope.status_code == 201, out_scope.text

    start = await anon_client.post(
        "/oauth/device_authorization",
        json={
            "client_id": "ak5-cli",
            "principal": "agent",
            "name": "scoped-worker",
            "workflow_role": "worker",
        },
    )
    device_code = start.json()["device_code"]
    user_code = start.json()["user_code"]
    csrf = await _login(client, monkeypatch)
    approve = await client.post(
        "/oauth/device/approve",
        json={
            "user_code": user_code,
            "approved": True,
            "workflow_role": "worker",
            "board_scope": ["proj-core-engine"],
        },
        headers={CSRF_HEADER: csrf},
    )
    assert approve.status_code == 200, approve.text
    token = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    opaque = token.json()["access_token"]
    agent_headers = {"Authorization": f"Bearer {opaque}"}

    ok = await anon_client.post(
        f"/api/v1/tickets/{in_scope.json()['ticket_id']}/claim",
        headers=agent_headers,
    )
    assert ok.status_code == 200, ok.text

    forbidden = await anon_client.post(
        f"/api/v1/tickets/{out_scope.json()['ticket_id']}/claim",
        headers=agent_headers,
    )
    assert forbidden.status_code == 403, forbidden.text
    body = forbidden.json()
    assert body.get("error", {}).get("code") == "ROLE_FORBIDDEN" or "board_scope" in str(
        body
    ).lower() or "ROLE_FORBIDDEN" in str(body)


@pytest.mark.asyncio
async def test_disabled_agent_token_rejected(
    client: AsyncClient,
    anon_client: AsyncClient,
    test_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    from datetime import UTC, datetime

    from ak5.server.models.workspace import Agent

    start = await anon_client.post(
        "/oauth/device_authorization",
        json={
            "client_id": "ak5-cli",
            "principal": "agent",
            "name": "soon-disabled",
            "workflow_role": "worker",
        },
    )
    device_code = start.json()["device_code"]
    user_code = start.json()["user_code"]
    csrf = await _login(client, monkeypatch)
    await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
        headers={CSRF_HEADER: csrf},
    )
    token = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    opaque = token.json()["access_token"]
    agent_id = token.json()["agent_id"]
    assert (
        await anon_client.get(
            "/api/v1/boards", headers={"Authorization": f"Bearer {opaque}"}
        )
    ).status_code == 200

    agent = await test_db.get(Agent, agent_id)
    assert agent is not None
    agent.disabled_at = datetime.now(UTC).replace(tzinfo=None)
    await test_db.commit()

    denied = await anon_client.get(
        "/api/v1/boards", headers={"Authorization": f"Bearer {opaque}"}
    )
    assert denied.status_code == 401


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


@pytest.mark.asyncio
async def test_agent_token_cannot_approve_or_refresh_as_user(
    client: AsyncClient,
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Agents must not mint user sessions or approve device grants (§4.3 / #25)."""
    start = await anon_client.post(
        "/oauth/device_authorization",
        json={
            "client_id": "ak5-cli",
            "principal": "agent",
            "name": "no-mint",
            "workflow_role": "worker",
        },
    )
    device_code = start.json()["device_code"]
    user_code = start.json()["user_code"]
    csrf = await _login(client, monkeypatch)
    await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
        headers={CSRF_HEADER: csrf},
    )
    token = await anon_client.post(
        "/oauth/token",
        json={
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": "ak5-cli",
        },
    )
    opaque = token.json()["access_token"]

    # Second grant — agent bearer must not approve (no web session).
    start2 = await anon_client.post(
        "/oauth/device_authorization",
        json={"client_id": "ak5-cli", "principal": "user"},
    )
    user_code2 = start2.json()["user_code"]
    agent_approve = await anon_client.post(
        "/oauth/device/approve",
        json={"user_code": user_code2, "approved": True},
        headers={"Authorization": f"Bearer {opaque}", CSRF_HEADER: "ignored"},
    )
    assert agent_approve.status_code == 401

    # Opaque agent token is not a CLI refresh token.
    refresh_attempt = await anon_client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "refresh_token": opaque},
    )
    assert refresh_attempt.status_code == 400
    assert refresh_attempt.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_legacy_device_routes_emit_deprecation(anon_client: AsyncClient):
    resp = await anon_client.post(
        "/auth/device/code",
        json={"actor_id": "legacy_dep_agent", "role": "Tester"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("deprecation") == "true"
    assert "/oauth/device_authorization" in (resp.headers.get("link") or "")
