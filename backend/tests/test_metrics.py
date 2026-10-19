"""#117: Prometheus metrics endpoint (/metrics) & collectors (operations.md §4.3)."""

from __future__ import annotations

import pytest
from ak5.server.connection_limit import connection_limiter
from ak5.server.main import app
from ak5.server.metrics import (
    Counter,
    Gauge,
    Histogram,
    claim_conflicts_total,
    lease_expirations_total,
    rate_limited_total,
    registry,
    workflow_transitions_total,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def _reset_metrics():
    registry.reset_all()
    connection_limiter.reset_for_testing()
    yield
    registry.reset_all()
    connection_limiter.reset_for_testing()


def test_cardinality_guard():
    """Operations.md §4.3: workspace_id, ticket_id, principal must not be used as metric labels."""
    with pytest.raises(ValueError, match="Forbidden label"):
        Counter("test_counter", "desc", ["workspace_id"])

    with pytest.raises(ValueError, match="Forbidden label"):
        Gauge("test_gauge", "desc", ["ticket_id"])

    with pytest.raises(ValueError, match="Forbidden label"):
        Histogram("test_hist", "desc", ["principal_id"])

    with pytest.raises(ValueError, match="Forbidden label"):
        Counter("test_counter_actor", "desc", ["actor_id"])

    with pytest.raises(ValueError, match="Forbidden label"):
        Gauge("test_gauge_board", "desc", ["board_id"])


def test_counter_primitives():
    c = Counter("test_counter", "Help text", ["method", "status"])
    c.inc(method="GET", status="200")
    c.inc(2.0, method="GET", status="200")
    c.inc(method="POST", status="201")

    lines = c.collect()
    assert "# HELP test_counter Help text" in lines
    assert "# TYPE test_counter counter" in lines
    assert 'test_counter{method="GET",status="200"} 3.0' in lines
    assert 'test_counter{method="POST",status="201"} 1.0' in lines

    with pytest.raises(ValueError):
        c.inc(-1.0, method="GET", status="200")


def test_gauge_primitives():
    g = Gauge("test_gauge", "Gauge help", ["kind"])
    g.set(5.0, kind="sse")
    g.inc(2.0, kind="sse")
    g.dec(1.0, kind="sse")
    g.set(10.0, kind="longpoll")

    lines = g.collect()
    assert "# HELP test_gauge Gauge help" in lines
    assert "# TYPE test_gauge gauge" in lines
    assert 'test_gauge{kind="sse"} 6.0' in lines
    assert 'test_gauge{kind="longpoll"} 10.0' in lines


def test_histogram_primitives():
    h = Histogram("test_latency", "Latency help", ["route"], buckets=(0.1, 0.5, 1.0))
    h.observe(0.05, route="/health")
    h.observe(0.25, route="/health")
    h.observe(1.5, route="/health")

    lines = h.collect()
    assert "# HELP test_latency Latency help" in lines
    assert "# TYPE test_latency histogram" in lines
    assert 'test_latency_bucket{le="0.1",route="/health"} 1' in lines
    assert 'test_latency_bucket{le="0.5",route="/health"} 2' in lines
    assert 'test_latency_bucket{le="1.0",route="/health"} 2' in lines
    assert 'test_latency_bucket{le="+Inf",route="/health"} 3' in lines
    assert 'test_latency_count{route="/health"} 3' in lines
    assert 'test_latency_sum{route="/health"} 1.8' in lines


async def test_metrics_endpoint_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AK5_METRICS_ENABLED", raising=False)
    transport = ASGITransport(app=app, client=("127.0.0.1", 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as loopback_client:
        resp = await loopback_client.get("/metrics")
        assert resp.status_code == 404


async def test_metrics_endpoint_loopback_access(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")
    monkeypatch.delenv("AK5_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("AK5_PROFILE", "local")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)

    # IPv4 loopback -> 200
    transport_v4 = ASGITransport(app=app, client=("127.0.0.1", 54321))
    async with AsyncClient(transport=transport_v4, base_url="http://test") as c_v4:
        resp = await c_v4.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain; version=0.0.4" in resp.headers["content-type"]
        assert "# HELP ak5_http_requests_total" in resp.text
        assert "# HELP ak5_active_connections" in resp.text

    # IPv6 loopback -> 200
    transport_v6 = ASGITransport(app=app, client=("::1", 54321))
    async with AsyncClient(transport=transport_v6, base_url="http://test") as c_v6:
        resp = await c_v6.get("/metrics")
        assert resp.status_code == 200

    # testclient is NOT in the allowlist -> 403
    transport_tc = ASGITransport(app=app, client=("testclient", 50000))
    async with AsyncClient(transport=transport_tc, base_url="http://test") as c_tc:
        resp_tc = await c_tc.get("/metrics")
        assert resp_tc.status_code == 403


async def test_metrics_endpoint_fail_closed_proxy_and_hosted_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")
    monkeypatch.delenv("AK5_METRICS_TOKEN", raising=False)

    transport = ASGITransport(app=app, client=("127.0.0.1", 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as loopback_client:
        # 1. AK5_PROFILE=hosted without token -> fail-closed (403)
        monkeypatch.setenv("AK5_PROFILE", "hosted")
        monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
        resp = await loopback_client.get("/metrics")
        assert resp.status_code == 403

        # 2. AK5_PROFILE=local but AK5_TRUSTED_PROXIES configured -> fail-closed (403)
        monkeypatch.setenv("AK5_PROFILE", "local")
        monkeypatch.setenv("AK5_TRUSTED_PROXIES", "127.0.0.1")
        resp = await loopback_client.get("/metrics")
        assert resp.status_code == 403


async def test_metrics_endpoint_remote_access_forbidden_without_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")
    monkeypatch.delenv("AK5_METRICS_TOKEN", raising=False)

    remote_transport = ASGITransport(app=app, client=("203.0.113.195", 54321))
    async with AsyncClient(transport=remote_transport, base_url="http://test") as remote_client:
        resp = await remote_client.get("/metrics")
        assert resp.status_code == 403


async def test_metrics_endpoint_token_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")
    monkeypatch.setenv("AK5_METRICS_TOKEN", "super-secret-metrics-token")

    remote_transport = ASGITransport(app=app, client=("203.0.113.195", 54321))
    async with AsyncClient(transport=remote_transport, base_url="http://test") as remote_client:
        # No token -> 403
        resp = await remote_client.get("/metrics")
        assert resp.status_code == 403

        # Invalid token -> 403
        resp = await remote_client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 403

        # Valid Bearer token -> 200
        resp = await remote_client.get("/metrics", headers={"Authorization": "Bearer super-secret-metrics-token"})
        assert resp.status_code == 200
        assert "# HELP ak5_http_requests_total" in resp.text

        # Query parameter token is dropped / rejected -> 403
        resp = await remote_client.get("/metrics?token=super-secret-metrics-token")
        assert resp.status_code == 403


async def test_metrics_http_and_connection_tracking(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")
    monkeypatch.setenv("AK5_PROFILE", "local")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("AK5_METRICS_TOKEN", raising=False)

    transport = ASGITransport(app=app, client=("127.0.0.1", 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as loopback_client:
        # Make a couple of requests to track
        await loopback_client.get("/health")
        await loopback_client.get("/meta")

        # Acquire connection slots
        s1 = connection_limiter.acquire("p1", "ws1", kind="sse")
        s2 = connection_limiter.acquire("p2", "ws1", kind="longpoll")

        resp = await loopback_client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text

        # Verify HTTP request metrics
        assert 'ak5_http_requests_total{method="GET",route="/health",status="200"}' in body
        assert 'ak5_http_requests_total{method="GET",route="/meta",status="200"}' in body

        # Verify active connections gauge
        assert 'ak5_active_connections{kind="all"} 2.0' in body
        assert 'ak5_active_connections{kind="sse"} 1.0' in body
        assert 'ak5_active_connections{kind="longpoll"} 1.0' in body

        s1.release()
        s2.release()


def test_metrics_event_sources():
    """Verify that domain error and workflow events increment their corresponding metrics."""
    # Claim conflict error metric
    claim_conflicts_total.inc()
    lines = claim_conflicts_total.collect()
    assert "ak5_claim_conflicts_total 1.0" in lines

    # Rate limited error metric
    rate_limited_total.inc(rule="ip_login")
    lines = rate_limited_total.collect()
    assert 'ak5_rate_limited_total{rule="ip_login"} 1.0' in lines

    # Workflow transition
    workflow_transitions_total.inc(op="claim_t2", from_stage="backlog", to_stage="in_progress")
    lines = workflow_transitions_total.collect()
    assert 'ak5_workflow_transitions_total{from_stage="backlog",op="claim_t2",to_stage="in_progress"} 1.0' in lines

    # Lease expirations
    lease_expirations_total.inc()
    lines = lease_expirations_total.collect()
    assert "ak5_lease_expirations_total 1.0" in lines


async def test_event_log_lag_seconds_metric(test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """Test ak5_event_log_lag_seconds gauge behavior (#137, operations.md §4.3)."""
    from datetime import UTC, datetime, timedelta

    from ak5.server.models.event import Event
    from ak5.server.models.subscription import Subscription
    from ak5.server.services.subscription_service import subscription_service

    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")
    monkeypatch.setenv("AK5_PROFILE", "local")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("AK5_METRICS_TOKEN", raising=False)

    transport = ASGITransport(app=app, client=("127.0.0.1", 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: No subscriptions exist -> lag 0.0
        lag = await subscription_service.compute_event_log_lag_seconds(test_db)
        assert lag == 0.0

        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "ak5_event_log_lag_seconds 0.0" in resp.text

        # Case 2: Subscription exists with ack_cursor = 0, but no events exist -> lag 0.0
        sub1 = Subscription(subscription_id="sub-1", ack_cursor=0)
        test_db.add(sub1)
        await test_db.commit()

        lag = await subscription_service.compute_event_log_lag_seconds(test_db)
        assert lag == 0.0

        # Case 3: Event 1 created 30 seconds ago
        past_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
        ev1 = Event(
            event_id="ev-1",
            type="TICKET_CREATED",
            created_at=past_time,
        )
        test_db.add(ev1)
        await test_db.commit()

        lag = await subscription_service.compute_event_log_lag_seconds(test_db)
        assert lag >= 29.0

        resp = await client.get("/metrics")
        assert resp.status_code == 200
        lines = [line for line in resp.text.splitlines() if line.startswith("ak5_event_log_lag_seconds ")]
        assert len(lines) == 1
        val = float(lines[0].split()[1])
        assert val >= 29.0

        # Case 4: Subscription acks event 1 -> lag drops to 0.0
        sub1.ack_cursor = ev1.seq
        await test_db.commit()

        lag = await subscription_service.compute_event_log_lag_seconds(test_db)
        assert lag == 0.0

        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "ak5_event_log_lag_seconds 0.0" in resp.text

        # Case 5 (#149): Dead consumer (lease expired minutes ago) continues to report growing lag,
        # and is excluded from the gauge only once it exceeds the abandonment window (24h).
        past_time_2 = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=50)
        ev2 = Event(event_id="ev-2", type="TICKET_UPDATED", created_at=past_time_2)
        test_db.add(ev2)
        # Sub1 lease expired 10 seconds ago: consumer is dead, but within the 24h abandonment window
        sub1.consumer_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10)
        await test_db.commit()

        # Dead consumer still alarms with growing lag (> 49.0s)
        lag_dead = await subscription_service.compute_event_log_lag_seconds(test_db)
        assert lag_dead >= 49.0

        # Once the subscription passes the abandonment window (e.g. 25 hours ago, or abandoned_hours=0.01),
        # it is dropped from the lag gauge so ancient dead subscriptions do not permanently pin the alarm
        sub1.consumer_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)
        await test_db.commit()

        lag_abandoned = await subscription_service.compute_event_log_lag_seconds(test_db)
        assert lag_abandoned == 0.0

        # Dynamic abandoned_hours override test
        sub1.consumer_lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        await test_db.commit()
        # With default 24h window: included -> lag >= 49.0
        assert await subscription_service.compute_event_log_lag_seconds(test_db) >= 49.0
        # With 1h window: expired 2h ago is past window -> excluded -> lag 0.0
        assert await subscription_service.compute_event_log_lag_seconds(test_db, abandoned_hours=1.0) == 0.0


async def test_route_ticket_missing_claim_token_does_not_increment_conflicts(test_db: AsyncSession):
    """Test #137 low-priority fix: missing claim_token in route_ticket is not counted as conflict."""
    from ak5.contracts.errors import ErrorCode
    from ak5.contracts.payloads import RoutePlan, RouteTargetBoard
    from ak5.server.models.actor import Actor
    from ak5.server.models.board import Board
    from ak5.server.models.column import Column
    from ak5.server.models.ticket import Ticket
    from ak5.server.seed import LOCAL_USER_ID, ensure_local_tenancy
    from ak5.server.services.workflow import WorkflowError, claim_ticket, route_ticket

    await ensure_local_tenancy(test_db)

    board = Board(board_id="b1", name="Test Board", created_by=LOCAL_USER_ID)
    col = Column(column_id="c1", board_id="b1", stage="unclassified", name="Inbox")
    ticket = Ticket(
        ticket_id="t1",
        board_id="b1",
        column_id="c1",
        title="Test Ticket",
        rank=0,
        created_by=LOCAL_USER_ID,
    )
    agent_actor = Actor(
        actor_id="agent1",
        name="Agent 1",
        actor_type="agent",
        role="triage",
    )
    worker_actor = Actor(
        actor_id="worker2",
        name="Worker 2",
        actor_type="agent",
        role="triage",
    )
    test_db.add_all([board, col, ticket, agent_actor, worker_actor])
    await test_db.commit()

    initial_conflicts = claim_conflicts_total._values.get((), 0.0)

    # Calling route_ticket without claim_token
    with pytest.raises(WorkflowError) as exc_info:
        await route_ticket(
            test_db,
            actor=agent_actor,
            ticket_id="t1",
            plan=RoutePlan(
                target=RouteTargetBoard(board_id="b1"),
                refined_title="T",
                refined_spec="S",
                acceptance_criteria=["C"],
            ),
            claim_token=None,
        )
    assert exc_info.value.code == ErrorCode.CLAIM_CONFLICT
    assert claim_conflicts_total._values.get((), 0.0) == initial_conflicts

    # Now claim ticket properly
    clm = await claim_ticket(test_db, actor=agent_actor, ticket_id="t1")
    assert clm["claim_token"]

    # Another worker attempting to claim the same ticket -> REAL claim conflict!
    with pytest.raises(WorkflowError) as exc_info:
        await claim_ticket(test_db, actor=worker_actor, ticket_id="t1")
    assert exc_info.value.code == ErrorCode.CLAIM_CONFLICT
    assert claim_conflicts_total._values.get((), 0.0) == initial_conflicts + 1.0


def test_safe_emit_helpers(caplog: pytest.LogCaptureFixture):
    """Test #132: safe_inc, safe_set, safe_dec, safe_observe suppress exceptions and log at DEBUG."""
    import logging

    from ak5.server.metrics import safe_dec, safe_inc, safe_observe, safe_set

    # 1. Normal execution
    c = Counter("test_safe_c", "Counter help", ["kind"])
    g = Gauge("test_safe_g", "Gauge help", ["kind"])
    h = Histogram("test_safe_h", "Hist help", ["kind"], buckets=(1.0, 5.0))

    safe_inc(c, 2.0, kind="req")
    assert c._values[(("kind", "req"),)] == 2.0

    safe_set(g, 10.0, kind="conn")
    assert g._values[(("kind", "conn"),)] == 10.0

    safe_dec(g, 3.0, kind="conn")
    assert g._values[(("kind", "conn"),)] == 7.0

    safe_observe(h, 2.5, kind="lat")
    assert h._totals[(("kind", "lat"),)] == 1

    # 2. None metric is safe no-op
    safe_inc(None, 1.0)
    safe_set(None, 1.0)
    safe_dec(None, 1.0)
    safe_observe(None, 1.0)

    # 3. Exceptions suppressed: ValueError at WARNING, general exceptions at DEBUG
    with caplog.at_level(logging.DEBUG):
        # Negative increment raises ValueError in Counter.inc -> logged at WARNING
        safe_inc(c, -5.0, kind="req")
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Invalid metric emission for test_safe_c" in r.message for r in warning_records)

        # Faulty gauge/histogram calls raise RuntimeError -> logged at DEBUG
        class BrokenMetric:
            name = "broken"

            def set(self, *a, **k):
                raise RuntimeError("Boom set")

            def dec(self, *a, **k):
                raise RuntimeError("Boom dec")

            def observe(self, *a, **k):
                raise RuntimeError("Boom observe")

        broken = BrokenMetric()
        safe_set(broken, 1.0)  # type: ignore[arg-type]
        assert any("Failed to set gauge" in record.message for record in caplog.records if record.levelno == logging.DEBUG)

        safe_dec(broken, 1.0)  # type: ignore[arg-type]
        assert any("Failed to decrement gauge" in record.message for record in caplog.records if record.levelno == logging.DEBUG)

        safe_observe(broken, 1.0)  # type: ignore[arg-type]
        assert any("Failed to observe histogram" in record.message for record in caplog.records if record.levelno == logging.DEBUG)

