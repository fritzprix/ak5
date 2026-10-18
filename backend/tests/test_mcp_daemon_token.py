"""#35 / client.md §7: the MCP adapter authenticates through the daemon profile."""

from __future__ import annotations

import httpx
import pytest
from ak5.client import auth as client_auth
from ak5.client.errors import ClientError
from ak5.contracts.errors import ErrorCode
from ak5.mcp.client import AK5APIError, AK5Client


def _client(handler, monkeypatch) -> tuple[AK5Client, list[tuple[str, str]]]:
    monkeypatch.delenv("AK5_ACTOR_TOKEN", raising=False)
    seen: list[tuple[str, str]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization", "")))
        return handler(request)

    client = AK5Client(base_url="http://test/api/v1")
    client._http = httpx.AsyncClient(
        base_url="http://test/api/v1", transport=httpx.MockTransport(_record)
    )
    return client, seen


@pytest.mark.asyncio
async def test_profile_token_used_and_identify_never_called(monkeypatch):
    monkeypatch.setattr(client_auth, "resolve_access_token", lambda: "tok-profile")
    client, seen = _client(lambda r: httpx.Response(200, json=[]), monkeypatch)
    await client._request("GET", "boards")
    assert seen == [("/api/v1/boards", "Bearer tok-profile")]


@pytest.mark.asyncio
async def test_401_reresolves_daemon_token_once(monkeypatch):
    tokens = iter(["tok-expired", "tok-fresh"])
    monkeypatch.setattr(client_auth, "resolve_access_token", lambda: next(tokens))
    client, seen = _client(
        lambda r: httpx.Response(401 if r.headers["authorization"] == "Bearer tok-expired" else 200, json=[]),
        monkeypatch,
    )
    resp = await client._request("GET", "boards")
    assert resp.status_code == 200
    assert [auth for _, auth in seen] == ["Bearer tok-expired", "Bearer tok-fresh"]


@pytest.mark.asyncio
async def test_pinned_token_is_not_reresolved(monkeypatch):
    monkeypatch.setattr(
        client_auth, "resolve_access_token", lambda: pytest.fail("pinned token must not re-resolve")
    )
    client, seen = _client(lambda r: httpx.Response(401, json={"error": {"code": "UNAUTHENTICATED", "message": "x"}}), monkeypatch)
    client.use_bearer("tok-pinned")
    with pytest.raises(AK5APIError):
        await client._request("GET", "boards")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_daemon_unavailable_surfaces_as_error_envelope(monkeypatch):
    def _boom():
        raise ClientError("daemon down", code=ErrorCode.DAEMON_UNAVAILABLE, exit_code=9)

    monkeypatch.setattr(client_auth, "resolve_access_token", _boom)
    client, seen = _client(lambda r: httpx.Response(200, json=[]), monkeypatch)
    with pytest.raises(AK5APIError) as info:
        await client._request("GET", "boards")
    assert info.value.code == ErrorCode.DAEMON_UNAVAILABLE
    assert seen == []


def test_base_url_follows_active_profile(monkeypatch, tmp_path):
    """With only AK5_PROFILE set, MCP talks to that profile's server (not localhost)."""
    from ak5.daemon.profiles import Profile, upsert_profile

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AK5_HOME", str(tmp_path / ".ak5"))
    monkeypatch.delenv("AK5_API_URL", raising=False)
    upsert_profile(Profile(name="remote", server="https://ak5.example.com/api/v1", role="worker"))
    monkeypatch.setenv("AK5_PROFILE", "remote")
    assert AK5Client().base_url == "https://ak5.example.com/api/v1"
    monkeypatch.setenv("AK5_API_URL", "http://override/api/v1")
    assert AK5Client().base_url == "http://override/api/v1"
