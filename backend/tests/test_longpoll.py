"""Regression tests for event long-poll, ack, consumer lease, retention (#11)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from ak5.config import settings
from ak5.contracts.events import EventType
from ak5.models.event import Event
from ak5.services.event_log import record_event
from ak5.services.subscription_service import (
    CommitLagVisibilityPolicy,
    NoOpVisibilityPolicy,
    SubscriptionService,
    build_visibility_policy,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _create_sub(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    board_id: str | None = "proj-core-engine",
    events: list[str] | None = None,
) -> str:
    payload: dict = {
        "filter": {
            "board_id": board_id,
            "events": events or ["TICKET_CREATED", "WORK_AVAILABLE"],
        }
    }
    res = await client.post("/api/v1/subscriptions", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["subscription_id"]


@pytest.mark.asyncio
async def test_create_subscription_filter_only_no_exec(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "filter": {
                "board_id": "inbox",
                "events": ["WORK_AVAILABLE"],
                "role": "triage",
                "capabilities": ["routing"],
                "for_actor": "agent_image_worker",
            }
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert "exec_command" not in body
    assert body["owner"] == "user_pm"
    assert body["filter"]["board_id"] == "inbox"
    assert body["filter"]["events"] == ["WORK_AVAILABLE"]
    assert body["filter"]["role"] == "triage"
    assert body["filter"]["capabilities"] == ["routing"]
    assert body["filter"]["for_actor"] == "agent_image_worker"
    assert body["ack_cursor"] == 0

    # Agents may register filter-only subscriptions (no server shell RCE).
    agent_headers = auth_headers("agent_image_worker", "agent")
    agent_res = await client.post(
        "/api/v1/subscriptions",
        json={"filter": {"board_id": "proj-core-engine", "events": ["TICKET_CREATED"]}},
        headers=agent_headers,
    )
    assert agent_res.status_code == 201, agent_res.text


@pytest.mark.asyncio
async def test_long_poll_returns_events_immediately(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)

    await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-1",
        actor_id="user_pm",
        data={"ticket": {"ticket_id": "TK-1", "title": "Hello"}},
    )
    await test_db.commit()

    res = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0, "limit": 10},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["events"]) >= 1
    assert body["events"][0]["type"] == "TICKET_CREATED"
    assert body["next_cursor"] == body["events"][-1]["seq"]


@pytest.mark.asyncio
async def test_long_poll_timeout_empty_array(client: AsyncClient, auth_headers, monkeypatch):
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)
    monkeypatch.setattr(settings, "LONG_POLL_MAX_WAIT", 2)

    started = asyncio.get_event_loop().time()
    res = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 1, "limit": 10, "cursor": 0},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
        timeout=5.0,
    )
    elapsed = asyncio.get_event_loop().time() - started
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["events"] == []
    assert body["next_cursor"] == 0
    assert elapsed >= 0.8


@pytest.mark.asyncio
async def test_long_poll_wakes_on_inprocess_notify(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)

    async def _publish_soon() -> None:
        await asyncio.sleep(0.3)
        await record_event(
            test_db,
            EventType.TICKET_CREATED,
            board_id="proj-core-engine",
            ticket_id="TK-wake",
            actor_id="user_pm",
            data={"ticket": {"ticket_id": "TK-wake", "title": "Wake"}},
        )
        await test_db.commit()

    task = asyncio.create_task(_publish_soon())
    started = asyncio.get_event_loop().time()
    res = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 10, "limit": 10, "cursor": 0},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
        timeout=15.0,
    )
    await task
    elapsed = asyncio.get_event_loop().time() - started
    assert res.status_code == 200, res.text
    body = res.json()
    assert any(e["type"] == "TICKET_CREATED" for e in body["events"])
    assert elapsed < 5.0  # woke via notify, not full wait


@pytest.mark.asyncio
async def test_ack_monotonic_and_resume_from_server_cursor(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)
    consumer = {"X-AK5-Consumer": "daemon-a"}

    for i in range(3):
        await record_event(
            test_db,
            EventType.TICKET_CREATED,
            board_id="proj-core-engine",
            ticket_id=f"TK-{i}",
            actor_id="user_pm",
            data={"n": i},
        )
    await test_db.commit()

    poll1 = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0, "limit": 2, "cursor": 0},
        headers={**headers, **consumer},
    )
    assert poll1.status_code == 200
    events = poll1.json()["events"]
    assert len(events) == 2
    ack_seq = events[-1]["seq"]

    ack = await client.post(
        f"/api/v1/subscriptions/{sub_id}/ack",
        json={"cursor": ack_seq},
        headers={**headers, **consumer},
    )
    assert ack.status_code == 200
    assert ack.json()["ack_cursor"] == ack_seq

    # Backward ack ignored
    ack_back = await client.post(
        f"/api/v1/subscriptions/{sub_id}/ack",
        json={"cursor": ack_seq - 1},
        headers={**headers, **consumer},
    )
    assert ack_back.status_code == 200
    assert ack_back.json()["ack_cursor"] == ack_seq

    # Resume without cursor → server ack_cursor
    poll2 = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0, "limit": 10},
        headers={**headers, **consumer},
    )
    assert poll2.status_code == 200
    remaining = poll2.json()["events"]
    assert all(e["seq"] > ack_seq for e in remaining)
    assert len(remaining) >= 1


@pytest.mark.asyncio
async def test_consumer_conflict(client: AsyncClient, auth_headers, monkeypatch):
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)
    monkeypatch.setattr(settings, "CONSUMER_LEASE_SECONDS", 60)

    ok = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert ok.status_code == 200

    conflict = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0},
        headers={**headers, "X-AK5-Consumer": "daemon-b"},
    )
    assert conflict.status_code == 409
    err = conflict.json()["error"]
    assert err["code"] == "CONSUMER_CONFLICT"

    # Same consumer renews OK
    renew = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert renew.status_code == 200


@pytest.mark.asyncio
async def test_cursor_expired_after_retention_purge(
    client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)
    monkeypatch.setattr(settings, "EVENT_RETENTION_DAYS", 7)

    old_a = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-old-a",
        actor_id="user_pm",
        data={"n": 1},
    )
    old_b = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-old-b",
        actor_id="user_pm",
        data={"n": 2},
    )
    await test_db.commit()

    # Age both events beyond retention so a gap appears after lazy purge
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
    for seq in (old_a.seq, old_b.seq):
        row = await test_db.get(Event, seq)
        assert row is not None
        row.created_at = cutoff
    await test_db.commit()

    new = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-new",
        actor_id="user_pm",
        data={"n": 3},
    )
    await test_db.commit()

    # Cursor inside the purged gap (processed old_a; old_b also gone) → CURSOR_EXPIRED
    res = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0, "cursor": old_a.seq},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert res.status_code == 410, res.text
    assert res.json()["error"]["code"] == "CURSOR_EXPIRED"
    assert new.seq > old_b.seq


@pytest.mark.asyncio
async def test_get_events_one_shot_poll(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    headers = auth_headers("user_pm", "human")
    e1 = await record_event(
        test_db,
        EventType.WORK_AVAILABLE,
        board_id="inbox",
        ticket_id=None,
        actor_id="system",
        data={
            "board_id": "inbox",
            "stage": "unclassified",
            "required_role": "triage",
            "capabilities": [],
        },
    )
    await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-x",
        actor_id="user_pm",
        data={},
    )
    await test_db.commit()

    all_res = await client.get("/api/v1/events", params={"since": 0, "limit": 50}, headers=headers)
    assert all_res.status_code == 200
    assert len(all_res.json()["events"]) >= 2

    inbox = await client.get(
        "/api/v1/events",
        params={"board_id": "inbox", "since": 0},
        headers=headers,
    )
    assert inbox.status_code == 200
    body = inbox.json()
    assert all(e["board_id"] == "inbox" for e in body["events"])
    assert body["next_cursor"] >= e1.seq


@pytest.mark.asyncio
async def test_no_subprocess_in_subscription_service():
    import inspect

    import ak5.services.subscription_service as mod

    source = inspect.getsource(mod)
    assert "create_subprocess" not in source
    assert "subprocess" not in source


@pytest.mark.asyncio
async def test_visibility_policy_injection_point(test_db: AsyncSession):
    """Postgres commit-lag guard is injectable; SQLite default is no-op."""
    noop = build_visibility_policy(window_seconds=0)
    assert isinstance(noop, NoOpVisibilityPolicy)
    assert await noop.max_visible_seq(test_db) is None

    e1 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id=None,
        actor_id="user_pm",
        data={"n": 1},
    )
    e2 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id=None,
        actor_id="user_pm",
        data={"n": 2},
    )
    await test_db.commit()

    # Make e2 "too new" relative to a 60s window by aging only e1.
    row1 = await test_db.get(Event, e1.seq)
    assert row1 is not None
    row1.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=120)
    await test_db.commit()

    lag = CommitLagVisibilityPolicy(window_seconds=60)
    ceiling = await lag.max_visible_seq(test_db)
    assert ceiling == e1.seq

    svc = SubscriptionService(visibility=lag)
    visible = await svc.fetch_events_since(test_db, cursor=0, limit=10)
    assert [e.seq for e in visible] == [e1.seq]
    assert e2.seq not in [e.seq for e in visible]


@pytest.mark.asyncio
async def test_missing_consumer_header_rejected(client: AsyncClient, auth_headers):
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)
    res = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0},
        headers=headers,
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "PAYLOAD_INVALID"
