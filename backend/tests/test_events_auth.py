"""SSE stream requires JWT via Bearer or ``access_token`` query."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from ak5.routers.auth import create_access_token
from httpx import AsyncClient


async def _empty_poll(*_args: Any, **_kwargs: Any) -> tuple[list, int]:
    return [], 0


async def _cancel_wait(_timeout: float = 0) -> bool:
    """End the SSE generator after CONNECTED so auth smoke tests finish."""
    raise asyncio.CancelledError


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
    monkeypatch.setattr("ak5.routers.events.subscription_service.poll_events", _empty_poll)
    monkeypatch.setattr("ak5.routers.events.event_bus.wait_for_notify", _cancel_wait)
    token = create_access_token("user_pm", "human")
    resp = await anon_client.get(
        "/api/v1/events/stream",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "CONNECTED" in body or "connected" in body.lower(), body[:500]


@pytest.mark.asyncio
async def test_sse_stream_accepts_access_token_query(anon_client: AsyncClient, monkeypatch):
    monkeypatch.setattr("ak5.routers.events.subscription_service.poll_events", _empty_poll)
    monkeypatch.setattr("ak5.routers.events.event_bus.wait_for_notify", _cancel_wait)
    token = create_access_token("user_pm", "human")
    resp = await anon_client.get(f"/api/v1/events/stream?access_token={token}", timeout=5.0)
    assert resp.status_code == 200
    body = resp.text
    assert "CONNECTED" in body or "connected" in body.lower(), body[:500]
