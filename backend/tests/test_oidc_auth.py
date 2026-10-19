"""Issue #24: OIDC PKCE login, local owner loopback, CSRF (identity.md §3/§8)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from ak5.server.auth.oidc import verify_id_token
from ak5.server.auth.oidc_config import OidcProvider
from ak5.server.auth.pending_state import (
    count_oidc_pending,
    inject_expired_oidc_pending_for_tests,
    mint_local_device_code_for_tests,
    store_oidc_pending,
)
from ak5.server.auth.sessions import (
    CSRF_COOKIE,
    CSRF_HEADER,
    OIDC_STATE_COOKIE,
    SESSION_COOKIE,
)
from ak5.server.seed import LOCAL_USER_ID
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

MOCK_ISSUER = "https://mock.ak5.test"
MOCK_SECRET = "mock-id-token-hs-secret-32bytes!!"
MOCK_CLIENT_ID = "ak5-test"


def _mock_providers_env() -> str:
    return json.dumps(
        [
            {
                "id": "mock",
                "issuer": MOCK_ISSUER,
                "client_id": MOCK_CLIENT_ID,
                "client_secret": "secret",
                "authorization_endpoint": f"{MOCK_ISSUER}/authorize",
                "token_endpoint": f"{MOCK_ISSUER}/token",
                "id_token_hs_secret": MOCK_SECRET,
            }
        ]
    )


def _id_token(*, nonce: str, aud: str = MOCK_CLIENT_ID, iss: str = MOCK_ISSUER) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "user-1",
            "iss": iss,
            "aud": aud,
            "nonce": nonce,
            "email": "user@example.com",
            "email_verified": True,
            "name": "Test User",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        MOCK_SECRET,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_local_owner_login_loopback_only(
    client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    ok = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert ok.status_code == 200
    assert ok.json()["user_id"] == LOCAL_USER_ID
    assert SESSION_COOKIE in ok.cookies
    assert CSRF_COOKIE in ok.cookies

    bad = await client.post("/auth/local/login", json={"password": "wrong"})
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_local_owner_login_rejects_non_loopback(
    test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    from ak5.server.main import app

    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    transport = ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/auth/local/login", json={"password": "local-secret"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_csrf_required_for_mutating_web_request(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert login.status_code == 200
    csrf = login.json()["csrf"]

    missing = await client.post("/auth/csrf-check")
    assert missing.status_code == 403

    ok = await client.post("/auth/csrf-check", headers={CSRF_HEADER: csrf})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True


@pytest.mark.asyncio
async def test_oidc_login_redirect_includes_pkce(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _mock_providers_env())
    resp = await client.get("/auth/login", params={"provider": "mock"}, follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith(f"{MOCK_ISSUER}/authorize")
    qs = parse_qs(urlparse(loc).query)
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"][0]
    assert qs["state"][0]
    assert qs["nonce"][0]
    assert "code_challenge" in qs
    assert resp.cookies.get(OIDC_STATE_COOKIE) == qs["state"][0]


@pytest.mark.asyncio
async def test_oidc_callback_success_and_nonce_rejection(
    client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _mock_providers_env())
    monkeypatch.setenv("AK5_PUBLIC_URL", "http://test")

    # Seed pending state as if /auth/login ran.
    state = "test-state"
    nonce = "test-nonce"
    await store_oidc_pending(
        test_db,
        client_key="test",
        state=state,
        provider_id="mock",
        nonce=nonce,
        code_verifier="verifier",
    )

    async def fake_exchange(*_a, **_k):
        return {"id_token": _id_token(nonce=nonce)}

    monkeypatch.setattr(
        "ak5.server.routers.oidc_auth.exchange_code",
        fake_exchange,
    )
    async def fake_discover(_p):
        return {
            "authorization_endpoint": f"{MOCK_ISSUER}/authorize",
            "token_endpoint": f"{MOCK_ISSUER}/token",
            "jwks_uri": "",
            "userinfo_endpoint": "",
        }

    monkeypatch.setattr(
        "ak5.server.routers.oidc_auth.discover_endpoints",
        fake_discover,
    )

    ok = await client.get(
        "/auth/callback",
        params={"code": "abc", "state": state},
        cookies={OIDC_STATE_COOKIE: state},
        follow_redirects=False,
    )
    assert ok.status_code == 302
    assert SESSION_COOKIE in ok.cookies

    # Bad nonce
    await store_oidc_pending(
        test_db,
        client_key="test",
        state="state2",
        provider_id="mock",
        nonce="expected-nonce",
        code_verifier="verifier",
    )

    async def fake_exchange_bad_nonce(*_a, **_k):
        return {"id_token": _id_token(nonce="wrong-nonce")}

    monkeypatch.setattr(
        "ak5.server.routers.oidc_auth.exchange_code",
        fake_exchange_bad_nonce,
    )
    bad = await client.get(
        "/auth/callback",
        params={"code": "abc", "state": "state2"},
        cookies={OIDC_STATE_COOKIE: "state2"},
        follow_redirects=False,
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_oidc_callback_rejects_missing_state_cookie(
    client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Login CSRF: callback URL without the browser-bound state cookie must fail."""
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _mock_providers_env())
    state = "stolen-state"
    await store_oidc_pending(
        test_db,
        client_key="test",
        state=state,
        provider_id="mock",
        nonce="n",
        code_verifier="verifier",
    )
    resp = await client.get(
        "/auth/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "state cookie" in resp.json()["detail"].lower()
    # Pending must remain so the legitimate browser can still complete.
    assert await count_oidc_pending(test_db) == 1


@pytest.mark.asyncio
async def test_oidc_callback_rejects_expired_state(
    client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _mock_providers_env())
    state = "expired-state"
    await inject_expired_oidc_pending_for_tests(test_db, state=state)
    resp = await client.get(
        "/auth/callback",
        params={"code": "abc", "state": state},
        cookies={OIDC_STATE_COOKIE: state},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower() or "invalid" in resp.json()[
        "detail"
    ].lower()


def test_verify_id_token_rejects_wrong_audience():
    provider = OidcProvider(
        id="mock",
        issuer=MOCK_ISSUER,
        client_id=MOCK_CLIENT_ID,
        id_token_hs_secret=MOCK_SECRET,
    )
    token = _id_token(nonce="n", aud="other-client")
    with pytest.raises(Exception):
        verify_id_token(provider, token, nonce="n")


def test_verify_id_token_happy_path():
    provider = OidcProvider(
        id="mock",
        issuer=MOCK_ISSUER,
        client_id=MOCK_CLIENT_ID,
        id_token_hs_secret=MOCK_SECRET,
    )
    token = _id_token(nonce="n")
    claims = verify_id_token(provider, token, nonce="n")
    assert claims.subject == "user-1"


@pytest.mark.asyncio
async def test_oidc_callback_rejects_missing_pkce_verifier(
    client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _mock_providers_env())
    await store_oidc_pending(
        test_db,
        client_key="test",
        state="no-pkce",
        provider_id="mock",
        nonce="n",
        code_verifier="",
    )
    resp = await client.get(
        "/auth/callback",
        params={"code": "abc", "state": "no-pkce"},
        cookies={OIDC_STATE_COOKIE: "no-pkce"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def _github_providers_env() -> str:
    return json.dumps(
        [
            {
                "id": "github",
                "issuer": "https://github.com",
                "client_id": "gh-client",
                "client_secret": "gh-secret",
            }
        ]
    )


@pytest.mark.asyncio
async def test_github_userinfo_callback_creates_session(
    client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _github_providers_env())
    monkeypatch.setenv("AK5_PUBLIC_URL", "http://test")
    await store_oidc_pending(
        test_db,
        client_key="test",
        state="gh-state",
        provider_id="github",
        nonce="unused",
        code_verifier="verifier",
    )

    async def fake_exchange(*_a, **_k):
        return {"access_token": "gho_test"}

    async def fake_userinfo(*_a, **_k):
        from ak5.server.auth.oidc import OidcClaims

        return OidcClaims(
            issuer="https://github.com",
            subject="12345",
            email="dev@users.noreply.github.com",
            email_verified=True,
            display_name="dev",
        )

    monkeypatch.setattr("ak5.server.routers.oidc_auth.exchange_code", fake_exchange)
    monkeypatch.setattr(
        "ak5.server.routers.oidc_auth.fetch_oauth_userinfo",
        fake_userinfo,
    )
    resp = await client.get(
        "/auth/callback",
        params={"code": "abc", "state": "gh-state"},
        cookies={OIDC_STATE_COOKIE: "gh-state"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert SESSION_COOKIE in resp.cookies


@pytest.mark.asyncio
async def test_github_login_omits_nonce_query(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _github_providers_env())
    resp = await client.get(
        "/auth/login", params={"provider": "github"}, follow_redirects=False
    )
    assert resp.status_code == 302
    qs = parse_qs(urlparse(resp.headers["location"]).query)
    assert "nonce" not in qs
    assert qs["code_challenge_method"] == ["S256"]


def test_warn_co_located_agent_env(monkeypatch: pytest.MonkeyPatch, capsys):
    from ak5.cli.commands.serve import warn_co_located_agent_env

    monkeypatch.delenv("AK5_AGENT", raising=False)
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    warn_co_located_agent_env()
    assert "Agent environment" not in capsys.readouterr().out

    monkeypatch.setenv("AK5_AGENT", "1")
    warn_co_located_agent_env()
    assert "identity.md §8.2" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_upstream_device_start_and_poll(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from ak5.server.auth.oidc import OidcClaims
    from ak5.server.auth.oidc_device import (
        UpstreamDevicePending,
    )

    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _github_providers_env())

    async def fake_start(db, provider, *, client_key):
        from ak5.server.auth.oidc_device import UpstreamDeviceStart

        code = await mint_local_device_code_for_tests(db, provider.id, interval=0, client_key=client_key)
        return UpstreamDeviceStart(
            device_code=code,
            user_code="ABCD-EFGH",
            verification_uri="https://github.com/login/device",
            verification_uri_complete=None,
            expires_in=900,
            interval=0,
        )

    monkeypatch.setattr(
        "ak5.server.routers.oidc_auth.start_upstream_device_flow",
        fake_start,
    )
    started = await client.post("/auth/oidc/device/start", params={"provider": "github"})
    assert started.status_code == 200
    body = started.json()
    assert body["user_code"] == "ABCD-EFGH"
    device_code = body["device_code"]

    calls = {"n": 0}

    async def pending_then_ok(db, provider, *, device_code: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise UpstreamDevicePending("authorization_pending", retry_after=1)
        return OidcClaims(
            issuer="https://github.com",
            subject="99",
            email="a@b.c",
            email_verified=True,
            display_name="a",
        )

    monkeypatch.setattr(
        "ak5.server.routers.oidc_auth.poll_upstream_device_flow",
        pending_then_ok,
    )
    pending = await client.post(
        "/auth/oidc/device/poll",
        json={"provider": "github", "device_code": device_code},
    )
    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"

    done = await client.post(
        "/auth/oidc/device/poll",
        json={"provider": "github", "device_code": device_code},
    )
    assert done.status_code == 200
    assert done.json()["success"] is True
    assert SESSION_COOKIE in done.cookies
