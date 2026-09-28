"""SSE stream requires JWT via Bearer or ``access_token`` query."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from ak5.routers.auth import create_access_token
from httpx import AsyncClient


async def _empty_subscribe(**_kwargs: Any) -> AsyncIterator[Any]:
    """End the SSE generator right after CONNECTED so the client is not stuck."""
    if False:  # pragma: no cover
        yield None
    return


@pytest.mark.asyncio
async def test_sse_stream_requires_auth(anon_client: AsyncClient):
    resp = await anon_client.get("/api/v1/events/stream")
    assert resp.status_code == 401
    assert "access_token" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sse_stream_rejects_invalid_token(anon_client: AsyncClient):
    resp = await anon_client.get("/api/v1/events/stream?access_token=not-a-jwt")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sse_stream_accepts_bearer(anon_client: AsyncClient, monkeypatch):
    monkeypatch.setattr("ak5.routers.events.event_bus.subscribe", _empty_subscribe)
    token = create_access_token("user_pm", "human")
    resp = await anon_client.get(
        "/api/v1/events/stream",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "CONNECTED" in body or "connected" in body.lower(), body[:500]


@pytest.mark.asyncio
async def test_sse_stream_accepts_access_token_query(anon_client: AsyncClient, monkeypatch):
    monkeypatch.setattr("ak5.routers.events.event_bus.subscribe", _empty_subscribe)
    token = create_access_token("user_pm", "human")
    resp = await anon_client.get(f"/api/v1/events/stream?access_token={token}")
    assert resp.status_code == 200
    body = resp.text
    assert "CONNECTED" in body or "connected" in body.lower(), body[:500]
