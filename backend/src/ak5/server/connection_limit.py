"""Long-poll and SSE connection limits (operations.md §2.5, #115, #130).

* Tracks active long-poll and SSE stream connections per instance (process), principal, and workspace.
* Off by default in local mode (``AK5_DEPLOY_PROFILE=local``).
  In ``AK5_DEPLOY_PROFILE=hosted``:
  - principal: 5 concurrent connections per instance (429 CONNECTION_LIMIT)
  - workspace: 50 concurrent connections per instance (429 CONNECTION_LIMIT)
  - instance: AK5_MAX_CONNECTIONS per process (default 500, 503 + Retry-After)
* Multi-instance deployments: enforced per-process in memory (aligned with Scale-by-Replicas, operations.md §4.3).
* Overrides: ``AK5_CONNECTION_LIMITS`` (JSON dict), ``AK5_MAX_CONNECTIONS`` (int).
"""

from __future__ import annotations

import collections
import functools
import json
import logging
import os
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.config import hosted_profile

logger = logging.getLogger(__name__)

HOSTED_CONNECTION_DEFAULTS = {
    "principal": 5,
    "workspace": 50,
    "instance": 500,
}

RETRY_AFTER_SECONDS = 5


class ConnectionLimitExceeded(Exception):
    def __init__(self, scope: str, limit: int, retry_after: int = RETRY_AFTER_SECONDS) -> None:
        self.scope = scope
        self.limit = limit
        self.retry_after = retry_after
        self.status_code = 503 if scope == "instance" else HTTP_STATUS[ErrorCode.CONNECTION_LIMIT]
        super().__init__(f"connection limit exceeded: {scope}")

    def body(self) -> dict[str, Any]:
        msg = (
            "Server connection limit reached; retry later."
            if self.scope == "instance"
            else f"Connection limit exceeded for {self.scope}; retry later."
        )
        return error_body(
            ErrorCode.CONNECTION_LIMIT,
            msg,
            details={"limit": self.limit, "scope": self.scope},
        )

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code, content=self.body(), headers=self.headers()
        )

    def http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=self.status_code, detail=self.body(), headers=self.headers()
        )


@functools.lru_cache(maxsize=16)
def _parse_connection_limits(hosted: bool, raw: str, max_conn_env: str) -> dict[str, int]:
    limits = dict(HOSTED_CONNECTION_DEFAULTS) if hosted else {}
    if max_conn_env:
        try:
            val = int(max_conn_env)
            if val > 0:
                limits["instance"] = val
            elif val == 0:
                limits.pop("instance", None)
        except ValueError:
            logger.warning("Ignoring invalid AK5_MAX_CONNECTIONS: %r", max_conn_env)
    if raw:
        try:
            overrides = json.loads(raw)
        except ValueError:
            logger.warning("Ignoring AK5_CONNECTION_LIMITS: not valid JSON")
            overrides = {}
        if not isinstance(overrides, dict):
            logger.warning("Ignoring AK5_CONNECTION_LIMITS: expected a JSON object")
            overrides = {}
        for name, value in overrides.items():
            if name not in HOSTED_CONNECTION_DEFAULTS:
                logger.warning("Ignoring unknown AK5_CONNECTION_LIMITS rule %r", name)
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                logger.warning("Ignoring AK5_CONNECTION_LIMITS[%r]: expected an integer >= 0", name)
            elif value == 0:
                limits.pop(name, None)
            else:
                limits[name] = value
    return limits


def active_connection_limits() -> dict[str, int]:
    return _parse_connection_limits(
        hosted_profile(),
        (os.environ.get("AK5_CONNECTION_LIMITS") or "").strip(),
        (os.environ.get("AK5_MAX_CONNECTIONS") or "").strip(),
    )


class ConnectionSlot:
    def __init__(self, limiter: ConnectionLimiter, principal_id: str, workspace_id: str, kind: str = "all") -> None:
        self._limiter = limiter
        self._principal_id = principal_id
        self._workspace_id = workspace_id
        self._kind = kind
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter._release(self._principal_id, self._workspace_id, self._kind)

    def __enter__(self) -> ConnectionSlot:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    async def __aenter__(self) -> ConnectionSlot:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class ConnectionLimiter:
    def __init__(self) -> None:
        self._instance_count: int = 0
        self._kind_counts: collections.defaultdict[str, int] = collections.defaultdict(int)
        self._principal_counts: collections.defaultdict[str, int] = collections.defaultdict(int)
        self._workspace_counts: collections.defaultdict[str, int] = collections.defaultdict(int)

    def acquire(self, principal_id: str, workspace_id: str, kind: str = "all") -> ConnectionSlot:
        limits = active_connection_limits()
        inst_limit = limits.get("instance")
        if inst_limit and self._instance_count >= inst_limit:
            raise ConnectionLimitExceeded("instance", inst_limit)

        p_limit = limits.get("principal")
        if p_limit and self._principal_counts[principal_id] >= p_limit:
            raise ConnectionLimitExceeded("principal", p_limit)

        ws_limit = limits.get("workspace")
        if ws_limit and self._workspace_counts[workspace_id] >= ws_limit:
            raise ConnectionLimitExceeded("workspace", ws_limit)

        self._instance_count += 1
        self._kind_counts[kind] += 1
        self._principal_counts[principal_id] += 1
        self._workspace_counts[workspace_id] += 1
        return ConnectionSlot(self, principal_id, workspace_id, kind)

    def _release(self, principal_id: str, workspace_id: str, kind: str = "all") -> None:
        self._instance_count = max(0, self._instance_count - 1)
        if kind in self._kind_counts:
            self._kind_counts[kind] = max(0, self._kind_counts[kind] - 1)
        if principal_id in self._principal_counts:
            self._principal_counts[principal_id] -= 1
            if self._principal_counts[principal_id] <= 0:
                del self._principal_counts[principal_id]
        if workspace_id in self._workspace_counts:
            self._workspace_counts[workspace_id] -= 1
            if self._workspace_counts[workspace_id] <= 0:
                del self._workspace_counts[workspace_id]

    def reset_for_testing(self) -> None:
        self._instance_count = 0
        self._kind_counts.clear()
        self._principal_counts.clear()
        self._workspace_counts.clear()
        _parse_connection_limits.cache_clear()

    @property
    def active_connections(self) -> int:
        return self._instance_count

    def count_for_kind(self, kind: str) -> int:
        return self._kind_counts[kind]


connection_limiter = ConnectionLimiter()
