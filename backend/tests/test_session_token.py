"""Issue #27: web session → Principal JWT exchange."""

from __future__ import annotations

import pytest
from ak5.server.auth.sessions import CSRF_HEADER, SESSION_COOKIE
from ak5.server.auth.tokens import verify_access_token
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
from ak5.server.seed import LOCAL_USER_ID, ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_session_token_mint_and_workspace_scope(
    client: AsyncClient,
    test_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "session-token-secret")

    login = await client.post(
        "/auth/local/login", json={"password": "session-token-secret"}
    )
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf"]
    assert SESSION_COOKIE in login.cookies

    minted = await client.post(
        "/auth/session/token",
        json={"workspace_id": LOCAL_WORKSPACE_ID},
        headers={CSRF_HEADER: csrf},
    )
    assert minted.status_code == 200, minted.text
    body = minted.json()
    assert body["workspace_id"] == LOCAL_WORKSPACE_ID
    assert body["user_id"] == LOCAL_USER_ID
    assert body["membership_role"] == "owner"

    principal = await verify_access_token(body["access_token"], test_db)
    assert principal.principal_id == LOCAL_USER_ID
    assert principal.workspace_id == LOCAL_WORKSPACE_ID

    bad_csrf = await client.post(
        "/auth/session/token",
        json={},
        headers={CSRF_HEADER: "wrong"},
    )
    assert bad_csrf.status_code == 403
