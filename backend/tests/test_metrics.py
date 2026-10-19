"""#117: Prometheus metrics endpoint (/metrics) & collectors (operations.md §4.3)."""

from __future__ import annotations

import pytest
from ak5.contracts.errors import ErrorCode
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
from ak5.server.ratelimit import RateLimited
from ak5.server.services.workflow import WorkflowError
from httpx import ASGITransport, AsyncClient


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


async def test_metrics_endpoint_disabled_by_default(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AK5_METRICS_ENABLED", raising=False)
    resp = await client.get("/metrics")
    assert resp.status_code == 404


async def test_metrics_endpoint_loopback_access(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")
    monkeypatch.delenv("AK5_METRICS_TOKEN", raising=False)

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain; version=0.0.4" in resp.headers["content-type"]
    assert "# HELP ak5_http_requests_total" in resp.text
    assert "# HELP ak5_active_connections" in resp.text


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

        # Valid query parameter token -> 200
        resp = await remote_client.get("/metrics?token=super-secret-metrics-token")
        assert resp.status_code == 200


async def test_metrics_http_and_connection_tracking(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_METRICS_ENABLED", "1")

    # Make a couple of requests to track
    await client.get("/health")
    await client.get("/meta")

    # Acquire connection slots
    s1 = connection_limiter.acquire("p1", "ws1", kind="sse")
    s2 = connection_limiter.acquire("p2", "ws1", kind="longpoll")

    resp = await client.get("/metrics")
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
    # Claim conflict error
    err = WorkflowError(ErrorCode.CLAIM_CONFLICT, "Ticket is already claimed")
    assert err.code == ErrorCode.CLAIM_CONFLICT
    lines = claim_conflicts_total.collect()
    assert "ak5_claim_conflicts_total 1.0" in lines

    # Rate limited error
    rl = RateLimited(rule="ip_login", limit=10, retry_after=60)
    assert rl.limit == 10
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
