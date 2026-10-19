"""Prometheus metrics exporter and collector (operations.md §4.3, #117).

* GET /metrics: Prometheus text format (version=0.0.4).
* Disabled by default: enabled only when AK5_METRICS_ENABLED=1.
* Authentication: AK5_METRICS_TOKEN (Bearer or ?token=) or loopback bind only.
* Cardinality guard: Labels MUST NOT include workspace_id, ticket_id, or principal identities.
"""

from __future__ import annotations

import collections
import hmac
import ipaddress
import logging
import os
import threading
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)

FORBIDDEN_LABELS = frozenset({
    "workspace_id",
    "ticket_id",
    "principal_id",
    "principal",
    "user_id",
    "actor_id",
    "board_id",
})

DEFAULT_HISTOGRAM_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
)


def _validate_labels(label_names: list[str]) -> None:
    for name in label_names:
        if name in FORBIDDEN_LABELS:
            raise ValueError(f"Forbidden label '{name}' violates cardinality rules (operations.md §4.3)")


def _format_labels(labels: dict[str, Any]) -> str:
    if not labels:
        return ""
    items = []
    for k in sorted(labels.keys()):
        val = str(labels[k]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        items.append(f'{k}="{val}"')
    return "{" + ",".join(items) + "}"


class Counter:
    def __init__(self, name: str, documentation: str, label_names: list[str] | None = None) -> None:
        self.name = name
        self.doc = documentation
        self.label_names = sorted(label_names or [])
        _validate_labels(self.label_names)
        self._values: collections.defaultdict[tuple[tuple[str, str], ...], float] = collections.defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        if amount < 0:
            raise ValueError("Counters can only be incremented by non-negative amounts")
        key = tuple(sorted((k, str(labels.get(k, ""))) for k in self.label_names))
        with self._lock:
            self._values[key] += amount

    def collect(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.doc}", f"# TYPE {self.name} counter"]
        with self._lock:
            if not self.label_names:
                lines.append(f"{self.name} {self._values.get((), 0.0)}")
            else:
                for key, val in sorted(self._values.items()):
                    lbl_dict = dict(key)
                    lines.append(f"{self.name}{_format_labels(lbl_dict)} {val}")
        return lines

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


class Gauge:
    def __init__(self, name: str, documentation: str, label_names: list[str] | None = None) -> None:
        self.name = name
        self.doc = documentation
        self.label_names = sorted(label_names or [])
        _validate_labels(self.label_names)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels: Any) -> None:
        key = tuple(sorted((k, str(labels.get(k, ""))) for k in self.label_names))
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        key = tuple(sorted((k, str(labels.get(k, ""))) for k in self.label_names))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: Any) -> None:
        self.inc(-amount, **labels)

    def collect(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.doc}", f"# TYPE {self.name} gauge"]
        with self._lock:
            if not self.label_names:
                lines.append(f"{self.name} {self._values.get((), 0.0)}")
            else:
                for key, val in sorted(self._values.items()):
                    lbl_dict = dict(key)
                    lines.append(f"{self.name}{_format_labels(lbl_dict)} {val}")
        return lines

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


class Histogram:
    def __init__(
        self,
        name: str,
        documentation: str,
        label_names: list[str] | None = None,
        buckets: tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS,
    ) -> None:
        self.name = name
        self.doc = documentation
        self.label_names = sorted(label_names or [])
        _validate_labels(self.label_names)
        self.buckets = tuple(sorted(buckets))
        self._counts: collections.defaultdict[tuple[tuple[str, str], ...], collections.defaultdict[float, int]] = (
            collections.defaultdict(lambda: collections.defaultdict(int))
        )
        self._sums: collections.defaultdict[tuple[tuple[str, str], ...], float] = collections.defaultdict(float)
        self._totals: collections.defaultdict[tuple[tuple[str, str], ...], int] = collections.defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, amount: float, **labels: Any) -> None:
        key = tuple(sorted((k, str(labels.get(k, ""))) for k in self.label_names))
        with self._lock:
            self._sums[key] += amount
            self._totals[key] += 1
            for b in self.buckets:
                if amount <= b:
                    self._counts[key][b] += 1

    def collect(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.doc}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key in sorted(self._totals.keys()):
                lbl_dict = dict(key)
                for b in self.buckets:
                    b_count = self._counts[key][b]
                    b_lbls = {**lbl_dict, "le": str(b)}
                    lines.append(f"{self.name}_bucket{_format_labels(b_lbls)} {b_count}")
                inf_lbls = {**lbl_dict, "le": "+Inf"}
                lines.append(f"{self.name}_bucket{_format_labels(inf_lbls)} {self._totals[key]}")
                lines.append(f"{self.name}_sum{_format_labels(lbl_dict)} {self._sums[key]}")
                lines.append(f"{self.name}_count{_format_labels(lbl_dict)} {self._totals[key]}")
        return lines

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._sums.clear()
            self._totals.clear()


class Registry:
    def __init__(self) -> None:
        self._collectors: list[Any] = []

    def register(self, collector: Any) -> Any:
        self._collectors.append(collector)
        return collector

    def collect_all(self) -> str:
        lines: list[str] = []
        for c in self._collectors:
            lines.extend(c.collect())
        return "\n".join(lines) + "\n"

    def reset_all(self) -> None:
        for c in self._collectors:
            c.reset()


registry = Registry()

# §4.3 metrics
http_requests_total = registry.register(
    Counter("ak5_http_requests_total", "Total HTTP requests", ["route", "method", "status"])
)
http_request_duration_seconds = registry.register(
    Histogram("ak5_http_request_duration_seconds", "HTTP request latency in seconds", ["route", "method", "status"])
)
workflow_transitions_total = registry.register(
    Counter("ak5_workflow_transitions_total", "Total workflow transitions", ["op", "from_stage", "to_stage"])
)
claim_conflicts_total = registry.register(
    Counter("ak5_claim_conflicts_total", "Total claim conflicts")
)
lease_expirations_total = registry.register(
    Counter("ak5_lease_expirations_total", "Total lease expirations")
)
escalations_total = registry.register(
    Counter("ak5_escalations_total", "Total escalations", ["kind"])
)
rate_limited_total = registry.register(
    Counter("ak5_rate_limited_total", "Total rate limited requests", ["rule"])
)
active_connections = registry.register(
    Gauge("ak5_active_connections", "Active long-poll or SSE connections", ["kind"])
)
db_pool_in_use = registry.register(
    Gauge("ak5_db_pool_in_use", "Database pool connections in use")
)
quota_rejections_total = registry.register(
    Counter("ak5_quota_rejections_total", "Total quota rejections", ["quota"])
)
event_log_lag_seconds = registry.register(
    Gauge("ak5_event_log_lag_seconds", "Event log consumer lag in seconds for the oldest unacknowledged subscription")
)


def metrics_enabled() -> bool:
    return (os.environ.get("AK5_METRICS_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def check_metrics_auth(request: Request) -> bool:
    token = (os.environ.get("AK5_METRICS_TOKEN") or "").strip()
    if token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            bearer_token = auth[7:].strip()
            return hmac.compare_digest(bearer_token.encode("utf-8"), token.encode("utf-8"))
        return False

    # No token configured: loopback fallback is only permitted in the 'local' profile
    # and only when no trusted reverse proxy is configured (fail-closed, #117, operations.md §4.3).
    profile = (os.environ.get("AK5_PROFILE") or "local").strip().lower()
    if profile != "local":
        return False

    from ak5.server.web_auth import trusted_proxy_networks

    if bool(trusted_proxy_networks()):
        return False

    if not request.client or not request.client.host:
        return False

    client_host = request.client.host
    try:
        ip = ipaddress.ip_address(client_host)
        return ip.is_loopback
    except ValueError:
        return False
