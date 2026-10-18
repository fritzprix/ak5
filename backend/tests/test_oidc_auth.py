"""Issue #24: OIDC PKCE login, local owner loopback, CSRF (identity.md §3/§8)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from ak5.server.auth.oidc import verify_id_token
from ak5.server.auth.oidc_config import OidcProvider
from ak5.server.auth.sessions import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    clear_oidc_pending_for_tests,
    store_oidc_pending,
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


@pytest.fixture(autouse=True)
def _clear_oidc_state():
    clear_oidc_pending_for_tests()
    yield
    clear_oidc_pending_for_tests()


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


@pytest.mark.asyncio
async def test_oidc_callback_success_and_nonce_rejection(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _mock_providers_env())
    monkeypatch.setenv("AK5_PUBLIC_URL", "http://test")

    # Seed pending state as if /auth/login ran.
    state = "test-state"
    nonce = "test-nonce"
    store_oidc_pending(
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
        follow_redirects=False,
    )
    assert ok.status_code == 302
    assert SESSION_COOKIE in ok.cookies

    # Bad nonce
    store_oidc_pending(
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
        follow_redirects=False,
    )
    assert bad.status_code == 401


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
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_OIDC_PROVIDERS", _mock_providers_env())
    store_oidc_pending(
        state="no-pkce",
        provider_id="mock",
        nonce="n",
        code_verifier="",
    )
    resp = await client.get(
        "/auth/callback",
        params={"code": "abc", "state": "no-pkce"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
