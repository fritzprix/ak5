"""Regression tests for event long-poll, ack, consumer lease, retention (#11)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from ak5.config import settings
from ak5.contracts.errors import ErrorCode
from ak5.contracts.events import EventType
from ak5.models.event import Event
from ak5.models.subscription import Subscription
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
from ak5.services.event_log import record_event
from ak5.services.subscription_service import (
    CommitLagVisibilityPolicy,
    NoOpVisibilityPolicy,
    SubscriptionService,
    SubscriptionServiceError,
    build_visibility_policy,
    reset_purge_throttle_for_tests,
)
from httpx import AsyncClient
from sqlalchemy import func, select
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
    monkeypatch.setattr(settings, "EVENT_PURGE_MIN_INTERVAL_SECONDS", 0.0)
    reset_purge_throttle_for_tests()

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

    # Ack up to old_b so the subscription has moved past old_b (allowing retention purge under safety rule)
    ack_res = await client.post(
        f"/api/v1/subscriptions/{sub_id}/ack",
        json={"cursor": old_b.seq},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert ack_res.status_code == 200

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
    visible, _ = await svc.fetch_events_since(test_db, cursor=0, limit=10)
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


@pytest.mark.asyncio
async def test_cursor_advances_past_filter_mismatch_wall(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    """High: non-matching events must not pin next_cursor (cursor starvation).

    500 TICKET_TRANSITIONED + 1 WORK_AVAILABLE behind a WORK_AVAILABLE filter
    must be reachable in a finite number of polls.
    """
    headers = auth_headers("user_pm", "human")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "filter": {"board_id": "inbox", "events": ["WORK_AVAILABLE"]},
            "start": "earliest",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    sub_id = res.json()["subscription_id"]

    for i in range(500):
        await record_event(
            test_db,
            EventType.TICKET_TRANSITIONED,
            board_id="inbox",
            ticket_id=f"TK-t-{i}",
            actor_id="user_pm",
            data={"n": i},
        )
    match = await record_event(
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
    await test_db.commit()

    cursor = 0
    found = False
    for _ in range(20):  # finite polls; old code looped forever with next_cursor stuck
        poll = await client.get(
            f"/api/v1/subscriptions/{sub_id}/events",
            params={"wait": 0, "limit": 50, "cursor": cursor},
            headers={**headers, "X-AK5-Consumer": "daemon-starvation"},
        )
        assert poll.status_code == 200, poll.text
        body = poll.json()
        assert body["next_cursor"] >= cursor
        if body["events"]:
            assert any(e["seq"] == match.seq for e in body["events"])
            found = True
            break
        # Empty batch must still advance past the mismatch wall.
        assert body["next_cursor"] > cursor
        cursor = body["next_cursor"]

    assert found, "WORK_AVAILABLE behind 500 mismatches was never reached"


@pytest.mark.asyncio
async def test_create_subscription_start_now_skips_history(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    """Medium: default start=now sets ack_cursor to max(seq), not 0."""
    headers = auth_headers("user_pm", "human")
    for i in range(5):
        await record_event(
            test_db,
            EventType.TICKET_CREATED,
            board_id="proj-core-engine",
            ticket_id=f"TK-hist-{i}",
            actor_id="user_pm",
            data={"n": i},
        )
    await test_db.commit()

    tip = (await test_db.execute(select(func.max(Event.seq)))).scalar_one()

    res = await client.post(
        "/api/v1/subscriptions",
        json={"filter": {"board_id": "proj-core-engine", "events": ["TICKET_CREATED"]}},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["ack_cursor"] == tip

    poll = await client.get(
        f"/api/v1/subscriptions/{res.json()['subscription_id']}/events",
        params={"wait": 0},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert poll.status_code == 200
    assert poll.json()["events"] == []

    earliest = await client.post(
        "/api/v1/subscriptions",
        json={
            "filter": {"board_id": "proj-core-engine", "events": ["TICKET_CREATED"]},
            "start": "earliest",
        },
        headers=headers,
    )
    assert earliest.status_code == 201
    assert earliest.json()["ack_cursor"] == 0


@pytest.mark.asyncio
async def test_ack_requires_consumer_when_lease_active(client: AsyncClient, auth_headers):
    """Medium: ack without X-AK5-Consumer is rejected while lease is held."""
    headers = auth_headers("user_pm", "human")
    sub_id = await _create_sub(client, headers)

    ok = await client.get(
        f"/api/v1/subscriptions/{sub_id}/events",
        params={"wait": 0},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert ok.status_code == 200

    no_hdr = await client.post(
        f"/api/v1/subscriptions/{sub_id}/ack",
        json={"cursor": 1},
        headers=headers,
    )
    assert no_hdr.status_code == 409
    assert no_hdr.json()["error"]["code"] == "CONSUMER_CONFLICT"

    wrong = await client.post(
        f"/api/v1/subscriptions/{sub_id}/ack",
        json={"cursor": 1},
        headers={**headers, "X-AK5-Consumer": "daemon-b"},
    )
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "CONSUMER_CONFLICT"

    good = await client.post(
        f"/api/v1/subscriptions/{sub_id}/ack",
        json={"cursor": 1},
        headers={**headers, "X-AK5-Consumer": "daemon-a"},
    )
    assert good.status_code == 200


@pytest.mark.asyncio
async def test_acquire_consumer_lease_is_atomic(test_db: AsyncSession, monkeypatch):
    """Medium: conditional UPDATE prevents two consumers claiming an empty lease."""
    monkeypatch.setattr(settings, "CONSUMER_LEASE_SECONDS", 60)
    sub = Subscription(
        subscription_id="sub-atomic-lease",
        workspace_id=LOCAL_WORKSPACE_ID,
        board_id="proj-core-engine",
        events="TICKET_CREATED",
        created_by="user_pm",
        ack_cursor=0,
    )
    test_db.add(sub)
    await test_db.commit()

    svc = SubscriptionService()
    await svc.acquire_consumer_lease(test_db, sub, "daemon-a")

    sub_b = await test_db.get(Subscription, "sub-atomic-lease")
    assert sub_b is not None
    with pytest.raises(SubscriptionServiceError) as exc_info:
        await svc.acquire_consumer_lease(test_db, sub_b, "daemon-b")
    assert exc_info.value.code == ErrorCode.CONSUMER_CONFLICT


@pytest.mark.asyncio
async def test_sse_starts_at_tip_without_last_event_id(
    client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    """Medium: SSE without Last-Event-ID must not replay the full retention log."""
    import asyncio

    from ak5.routers.auth import create_access_token

    headers = auth_headers("user_pm", "human")
    _ = headers
    for i in range(10):
        await record_event(
            test_db,
            EventType.TICKET_CREATED,
            board_id="proj-core-engine",
            ticket_id=f"TK-sse-{i}",
            actor_id="user_pm",
            data={"n": i},
        )
    await test_db.commit()

    async def _cancel_wait(_timeout: float = 0) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr("ak5.routers.events.event_bus.wait_for_notify", _cancel_wait)
    token = create_access_token("user_pm", "human")
    resp = await client.get(
        "/api/v1/events/stream",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "CONNECTED" in body or "connected" in body.lower()
    # Historical TICKET_CREATED rows must not be replayed on connect.
    assert "TK-sse-0" not in body
    assert "TICKET_CREATED" not in body or body.count("TICKET_CREATED") == 0


@pytest.mark.asyncio
async def test_sse_resync_on_cursor_expired(
    client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    """Medium: CURSOR_EXPIRED on SSE resets to max(seq) and emits RESYNC."""
    import asyncio

    from ak5.routers.auth import create_access_token
    from ak5.services.subscription_service import subscription_service

    monkeypatch.setattr(settings, "EVENT_PURGE_MIN_INTERVAL_SECONDS", 0.0)
    reset_purge_throttle_for_tests()

    e1 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-resync-1",
        actor_id="user_pm",
        data={},
    )
    await test_db.commit()
    tip = e1.seq

    calls = {"n": 0}

    async def _poll_then_expire(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SubscriptionServiceError(ErrorCode.CURSOR_EXPIRED, "expired", details={})
        return [], int(kwargs.get("since", 0) or 0)

    async def _cancel_wait(_timeout: float = 0) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr(subscription_service, "poll_events", _poll_then_expire)
    monkeypatch.setattr("ak5.routers.events.event_bus.wait_for_notify", _cancel_wait)

    token = create_access_token("user_pm", "human")
    resp = await client.get(
        "/api/v1/events/stream",
        headers={
            "Authorization": f"Bearer {token}",
            "Last-Event-ID": "1",
        },
        timeout=5.0,
    )
    assert resp.status_code == 200
    assert "RESYNC" in resp.text
    assert "CURSOR_EXPIRED" in resp.text
    assert str(tip) in resp.text


@pytest.mark.asyncio
async def test_cursor_expired_uses_purged_through_not_seq_holes(
    test_db: AsyncSession, monkeypatch
):
    """Low: seq holes alone must not raise CURSOR_EXPIRED; only purged_through does."""
    from sqlalchemy import delete

    monkeypatch.setattr(settings, "EVENT_RETENTION_DAYS", 7)
    monkeypatch.setattr(settings, "EVENT_PURGE_MIN_INTERVAL_SECONDS", 0.0)
    reset_purge_throttle_for_tests()

    svc = SubscriptionService()
    e1 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-hole-1",
        actor_id="user_pm",
        data={},
    )
    e2 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-hole-2",
        actor_id="user_pm",
        data={},
    )
    e3 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-hole-3",
        actor_id="user_pm",
        data={},
    )
    await test_db.commit()

    # Raw delete creates a min(seq) gap without recording purged_through —
    # old min_seq > cursor+1 logic would false-positive CURSOR_EXPIRED here.
    await test_db.execute(delete(Event).where(Event.seq.in_([e1.seq, e2.seq])))
    await test_db.commit()
    await svc.ensure_cursor_valid(test_db, cursor=e1.seq)

    # Real retention purge must expire cursors below purged_through_seq.
    row = await test_db.get(Event, e3.seq)
    assert row is not None
    row.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
    await test_db.commit()

    newer = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-after-purge",
        actor_id="user_pm",
        data={},
    )
    await test_db.commit()

    deleted = await svc.purge_expired_events(test_db)
    await test_db.commit()
    assert deleted >= 1
    purged_through = await svc.get_purged_through_seq(test_db)
    assert purged_through >= e3.seq

    stale = max(1, purged_through - 1)
    with pytest.raises(SubscriptionServiceError) as ei:
        await svc.ensure_cursor_valid(test_db, cursor=stale)
    assert ei.value.code == ErrorCode.CURSOR_EXPIRED

    await svc.ensure_cursor_valid(test_db, cursor=purged_through)
    assert newer.seq > purged_through


@pytest.mark.asyncio
async def test_purge_throttled_per_process(test_db: AsyncSession, monkeypatch):
    """Low: purge runs at most once per EVENT_PURGE_MIN_INTERVAL_SECONDS."""
    monkeypatch.setattr(settings, "EVENT_RETENTION_DAYS", 7)
    monkeypatch.setattr(settings, "EVENT_PURGE_MIN_INTERVAL_SECONDS", 60.0)
    reset_purge_throttle_for_tests()

    e = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-throttle",
        actor_id="user_pm",
        data={},
    )
    await test_db.commit()
    row = await test_db.get(Event, e.seq)
    assert row is not None
    row.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
    await test_db.commit()

    svc = SubscriptionService()
    first = await svc.purge_expired_events(test_db)
    await test_db.commit()
    assert first >= 1

    # Re-insert an expired row; throttle should skip the second purge.
    e2 = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-throttle-2",
        actor_id="user_pm",
        data={},
    )
    await test_db.commit()
    row2 = await test_db.get(Event, e2.seq)
    assert row2 is not None
    row2.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
    await test_db.commit()

    second = await svc.purge_expired_events(test_db)
    assert second == 0
    still = await test_db.get(Event, e2.seq)
    assert still is not None


@pytest.mark.asyncio
async def test_sse_stream_filters_by_board_id(
    client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    """Board A stream must not include board B events (#31)."""
    import asyncio

    from ak5.routers.auth import create_access_token
    from ak5.services.subscription_service import subscription_service

    tip_before = await subscription_service.max_event_seq(test_db)

    await record_event(
        test_db,
        EventType.TICKET_TRANSITIONED,
        board_id="proj-core-engine",
        ticket_id="TK-board-a",
        actor_id="user_pm",
        data={"board_id": "proj-core-engine"},
    )
    await record_event(
        test_db,
        EventType.TICKET_TRANSITIONED,
        board_id="board-other-sse",
        ticket_id="TK-board-b",
        actor_id="user_pm",
        data={"board_id": "board-other-sse"},
    )
    await test_db.commit()

    async def _cancel_wait(_timeout: float = 0) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr("ak5.routers.events.event_bus.wait_for_notify", _cancel_wait)
    token = create_access_token("user_pm", "human")
    resp = await client.get(
        "/api/v1/events/stream?board_id=proj-core-engine",
        headers={
            "Authorization": f"Bearer {token}",
            "Last-Event-ID": str(tip_before),
        },
        timeout=5.0,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "CONNECTED" in body
    assert "TK-board-a" in body
    assert "TK-board-b" not in body
    assert "board-other-sse" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["route", "bounce", "reroute"])
async def test_board_move_transition_visible_on_origin_board_sse(
    op: str, client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    """Board moves dual-emit TICKET_TRANSITIONED so the origin board SSE sees the leave (#31)."""
    import json

    from ak5.models.actor import Actor
    from ak5.routers.auth import create_access_token
    from helpers_workflow import board_ticket, inbox_ticket

    for actor_id, role, atype in (
        ("agent_triage_sse", "triage", "agent"),
        ("agent_worker_sse", "worker", "agent"),
    ):
        if await test_db.get(Actor, actor_id) is None:
            test_db.add(
                Actor(
                    actor_id=actor_id,
                    actor_type=atype,
                    name=actor_id,
                    role=role,
                    capabilities=json.dumps([]),
                    status="idle",
                )
            )
    await test_db.commit()

    human = auth_headers("user_pm", "human")
    triage = auth_headers("agent_triage_sse", "agent")
    worker = auth_headers("agent_worker_sse", "agent")

    tip_before = int((await test_db.execute(select(func.max(Event.seq)))).scalar_one() or 0)

    if op == "route":
        tid = (
            await client.post(
                "/api/v1/tickets",
                json=inbox_ticket("sse route leave"),
                headers=human,
            )
        ).json()["ticket_id"]
        token = (await client.post(f"/api/v1/tickets/{tid}/claim", headers=triage)).json()[
            "claim_token"
        ]
        origin = "inbox"
        moved = await client.post(
            f"/api/v1/tickets/{tid}/route",
            json={
                "claim_token": token,
                "plan": {
                    "refined_title": "routed",
                    "refined_spec": "spec",
                    "acceptance_criteria": ["c"],
                    "target": {"board_id": "proj-core-engine"},
                    "confidence": 0.9,
                },
            },
            headers=triage,
        )
        assert moved.status_code == 200, moved.text
    elif op == "bounce":
        tid = (
            await client.post(
                "/api/v1/tickets",
                json=board_ticket("sse bounce leave"),
                headers=human,
            )
        ).json()["ticket_id"]
        claim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
        assert claim.status_code == 200, claim.text
        tip_before = int((await test_db.execute(select(func.max(Event.seq)))).scalar_one() or 0)
        origin = "proj-core-engine"
        bounced = await client.post(
            f"/api/v1/tickets/{tid}/bounce",
            json={"claim_token": claim.json()["claim_token"], "reason": "wrong board"},
            headers=worker,
        )
        assert bounced.status_code == 200, bounced.text
    else:
        tid = (
            await client.post(
                "/api/v1/tickets",
                json=board_ticket("sse reroute leave"),
                headers=human,
            )
        ).json()["ticket_id"]
        tip_before = int((await test_db.execute(select(func.max(Event.seq)))).scalar_one() or 0)
        origin = "proj-core-engine"
        rerouted = await client.post(
            f"/api/v1/tickets/{tid}/reroute",
            json={"reason": "send home", "to_inbox": True},
            headers=human,
        )
        assert rerouted.status_code == 200, rerouted.text

    async def _cancel_wait(_timeout: float = 0) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr("ak5.routers.events.event_bus.wait_for_notify", _cancel_wait)

    token = create_access_token("user_pm", "human")
    resp = await client.get(
        "/api/v1/events/stream",
        params={"board_id": origin, "last_event_id": tip_before},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    assert resp.status_code == 200, resp.text
    assert "TICKET_TRANSITIONED" in resp.text
    assert tid in resp.text
    assert "from_board_id" in resp.text


@pytest.mark.asyncio
async def test_sse_last_event_id_query_param_resumes(
    client: AsyncClient, auth_headers, test_db: AsyncSession, monkeypatch
):
    """Reconnect via last_event_id= query (EventSource cannot set Last-Event-ID header)."""
    from ak5.routers.auth import create_access_token

    e = await record_event(
        test_db,
        EventType.TICKET_CREATED,
        board_id="proj-core-engine",
        ticket_id="TK-query-resume",
        actor_id="user_pm",
        data={},
    )
    await test_db.commit()

    async def _cancel_wait(_timeout: float = 0) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr("ak5.routers.events.event_bus.wait_for_notify", _cancel_wait)
    token = create_access_token("user_pm", "human")
    resp = await client.get(
        "/api/v1/events/stream",
        params={"board_id": "proj-core-engine", "last_event_id": e.seq - 1 if e.seq > 0 else 0},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    assert resp.status_code == 200
    assert "TK-query-resume" in resp.text
