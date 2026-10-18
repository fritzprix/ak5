"""Issue #64: /docs and /openapi.json require web session when the gate is on."""

from __future__ import annotations

import pytest
from ak5.server.auth.sessions import SESSION_COOKIE
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_docs_and_openapi_require_session_when_gate_on(
    client: AsyncClient,
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "docs-gate-secret")
    monkeypatch.delenv("AK5_PUBLIC_DOCS", raising=False)

    blocked_docs = await anon_client.get("/docs", headers={"Accept": "application/json"})
    assert blocked_docs.status_code == 401, blocked_docs.text

    blocked_openapi = await anon_client.get("/openapi.json")
    assert blocked_openapi.status_code == 401, blocked_openapi.text

    login = await client.post("/auth/local/login", json={"password": "docs-gate-secret"})
    assert login.status_code == 200, login.text
    assert SESSION_COOKIE in login.cookies

    ok_docs = await client.get("/docs", headers={"Accept": "text/html"})
    assert ok_docs.status_code == 200, ok_docs.text

    ok_openapi = await client.get("/openapi.json")
    assert ok_openapi.status_code == 200, ok_openapi.text
    assert "openapi" in ok_openapi.json()


@pytest.mark.asyncio
async def test_public_docs_env_bypasses_gate(
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "docs-gate-secret")
    monkeypatch.setenv("AK5_PUBLIC_DOCS", "1")

    docs = await anon_client.get("/docs", headers={"Accept": "text/html"})
    assert docs.status_code == 200, docs.text

    openapi = await anon_client.get("/openapi.json")
    assert openapi.status_code == 200, openapi.text


@pytest.mark.asyncio
async def test_garbage_session_cookie_does_not_unlock_docs(
    anon_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "docs-gate-secret")
    monkeypatch.delenv("AK5_PUBLIC_DOCS", raising=False)

    anon_client.cookies.set(SESSION_COOKIE, "not-a-real.session")
    blocked = await anon_client.get("/openapi.json")
    assert blocked.status_code == 401, blocked.text
