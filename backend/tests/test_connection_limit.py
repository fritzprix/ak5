"""#115: Long-poll / SSE connection limits (operations.md §2.5)."""

from __future__ import annotations

import asyncio
import json

import pytest
from ak5.contracts.errors import EXIT_CODE, HTTP_STATUS, ErrorCode
from ak5.server.connection_limit import (
    ConnectionLimitExceeded,
    active_connection_limits,
    connection_limiter,
)
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _reset_limiter():
    connection_limiter.reset_for_testing()
    yield
    connection_limiter.reset_for_testing()


def test_connection_limits_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    monkeypatch.delenv("AK5_CONNECTION_LIMITS", raising=False)
    monkeypatch.delenv("AK5_MAX_CONNECTIONS", raising=False)
    assert active_connection_limits() == {}

    monkeypatch.setenv("AK5_PROFILE", "hosted")
    assert active_connection_limits() == {
        "principal": 5,
        "workspace": 50,
        "instance": 500,
    }

    monkeypatch.setenv("AK5_MAX_CONNECTIONS", "100")
    assert active_connection_limits()["instance"] == 100

    monkeypatch.setenv("AK5_CONNECTION_LIMITS", json.dumps({"principal": 2, "workspace": 0}))
    limits = active_connection_limits()
    assert limits["principal"] == 2
    assert "workspace" not in limits
    assert limits["instance"] == 100


def test_connection_limit_contracts():
    assert HTTP_STATUS[ErrorCode.CONNECTION_LIMIT] == 429
    assert EXIT_CODE[ErrorCode.CONNECTION_LIMIT] == 3

    exc_p = ConnectionLimitExceeded("principal", 5)
    assert exc_p.status_code == 429
    assert exc_p.body()["error"]["code"] == "CONNECTION_LIMIT"
    assert exc_p.body()["error"]["details"]["scope"] == "principal"
    assert exc_p.headers() == {"Retry-After": "5"}

    exc_inst = ConnectionLimitExceeded("instance", 500)
    assert exc_inst.status_code == 503
    assert exc_inst.body()["error"]["code"] == "CONNECTION_LIMIT"
    assert exc_inst.body()["error"]["details"]["scope"] == "instance"
    assert exc_inst.headers() == {"Retry-After": "5"}


def test_connection_limiter_unit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    monkeypatch.setenv("AK5_CONNECTION_LIMITS", json.dumps({"principal": 2, "workspace": 3, "instance": 4}))

    # 2 connections for p1 in ws1
    s1 = connection_limiter.acquire("p1", "ws1")
    s2 = connection_limiter.acquire("p1", "ws1")
    assert connection_limiter.active_connections == 2

    # 3rd connection for p1 exceeds principal limit
    with pytest.raises(ConnectionLimitExceeded) as exc_info:
        connection_limiter.acquire("p1", "ws1")
    assert exc_info.value.scope == "principal"
    assert exc_info.value.limit == 2

    # p2 in ws1 succeeds (principal count is per-principal)
    s3 = connection_limiter.acquire("p2", "ws1")
    assert connection_limiter.active_connections == 3

    # 4th connection in ws1 exceeds workspace limit (limit=3)
    with pytest.raises(ConnectionLimitExceeded) as exc_info:
        connection_limiter.acquire("p3", "ws1")
    assert exc_info.value.scope == "workspace"
    assert exc_info.value.limit == 3

    # Release s1 allows another connection in ws1
    s1.release()
    assert connection_limiter.active_connections == 2
    s4 = connection_limiter.acquire("p3", "ws1")
    assert connection_limiter.active_connections == 3

    # Now acquire in ws2 up to instance limit (limit=4)
    s5 = connection_limiter.acquire("p4", "ws2")
    assert connection_limiter.active_connections == 4

    # 5th connection exceeds instance limit
    with pytest.raises(ConnectionLimitExceeded) as exc_info:
        connection_limiter.acquire("p5", "ws2")
    assert exc_info.value.scope == "instance"
    assert exc_info.value.status_code == 503

    # Releasing all clears state
    s2.release()
    s3.release()
    s4.release()
    s5.release()
    assert connection_limiter.active_connections == 0


@pytest.mark.asyncio
async def test_long_poll_connection_limit_endpoint(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    monkeypatch.setenv("AK5_CONNECTION_LIMITS", json.dumps({"principal": 1}))

    # Create a subscription
    resp = await client.post(
        "/api/v1/subscriptions",
        json={"topic": "tickets", "events": ["TICKET_CREATED"]},
    )
    assert resp.status_code == 201, resp.text
    sub_id = resp.json()["subscription_id"]

    # Acquire a slot manually for user_pm to saturate principal limit (limit=1)
    slot = connection_limiter.acquire("user_pm", "default")
    try:
        poll_resp = await client.get(
            f"/api/v1/subscriptions/{sub_id}/events",
            headers={"X-AK5-Consumer": "worker-1"},
            params={"wait": 0},
        )
        assert poll_resp.status_code == 429
        data = poll_resp.json()
        assert data["error"]["code"] == "CONNECTION_LIMIT"
        assert data["error"]["details"]["scope"] == "principal"
        assert poll_resp.headers["Retry-After"] == "5"
    finally:
        slot.release()

    # After release, long-poll succeeds
    poll_resp2 = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        headers={"X-AK5-Consumer": "worker-1"},
        params={"wait": 0},
    )
    assert poll_resp2.status_code == 200
    # Counter should be back to 0
    assert connection_limiter.active_connections == 0


@pytest.mark.asyncio
async def test_sse_stream_connection_limit_endpoint(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    monkeypatch.setenv("AK5_CONNECTION_LIMITS", json.dumps({"principal": 1}))

    # Saturate principal limit for user_pm
    slot = connection_limiter.acquire("user_pm", "default")
    try:
        resp = await client.get("/api/v1/events/stream")
        assert resp.status_code == 429
        data = resp.json()
        assert data["error"]["code"] == "CONNECTION_LIMIT"
        assert data["error"]["details"]["scope"] == "principal"
    finally:
        slot.release()

    # Slot released allows connection
    assert connection_limiter.active_connections == 0


@pytest.mark.asyncio
async def test_sse_stream_acquire_and_release_lifecycle(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """#115 review: opening a stream increments active connections, and disconnecting releases it."""
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    monkeypatch.setenv("AK5_CONNECTION_LIMITS", json.dumps({"principal": 2}))

    connected_observed = False

    async def _check_and_cancel(*_a, **_kw):
        nonlocal connected_observed
        if connection_limiter.active_connections == 1:
            connected_observed = True
        raise asyncio.CancelledError

    monkeypatch.setattr("ak5.server.routers.events.event_bus.wait_for_notify", _check_and_cancel)

    assert connection_limiter.active_connections == 0
    resp = await client.get("/api/v1/events/stream")
    assert resp.status_code == 200
    assert "CONNECTED" in resp.text
    assert connected_observed is True
    assert connection_limiter.active_connections == 0


@pytest.mark.asyncio
async def test_sse_stream_early_disconnect_background_release():
    """#115 review: background task releases the slot even if generator never runs."""
    slot = connection_limiter.acquire("user_pm", "default")
    assert connection_limiter.active_connections == 1

    from starlette.background import BackgroundTask

    task = BackgroundTask(slot.release)
    await task()
    assert connection_limiter.active_connections == 0


