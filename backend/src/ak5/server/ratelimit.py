"""Request rate limits (operations.md §2, #114).

* Fixed window (60 s) counters in the ``rate_counters`` table, bumped with one atomic upsert,
  so every instance shares one budget and nothing lives in process memory.
* Disabled by default (``AK5_PROFILE=local``): a single-user tool does not throttle itself.
  ``AK5_PROFILE=hosted`` turns on the defaults below; ``AK5_RATE_LIMITS`` (JSON, ``0`` = off)
  overrides either way.
* Authenticated routes are limited per principal / workspace right after the token is resolved
  (``enforce_authenticated``); unauthenticated entry points per client IP (``RateLimitMiddleware``).
  Long-poll / SSE streams are exempt (they have connection limits instead, #115).
* Exceeding a limit is ``429 RATE_LIMITED`` + ``Retry-After``; the body names the rule's scope and
  limit but never anyone else's usage.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.metrics import rate_limited_total, safe_inc
from ak5.server.models.rate_counter import RateCounter
from ak5.server.web_auth import client_ip_from_headers

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60

#: Rule → requests per window under ``AK5_PROFILE=hosted`` (operations.md §2.3).
HOSTED_DEFAULTS: dict[str, int] = {
    "read_principal": 300,
    "write_principal": 120,
    "write_workspace": 600,
    "upload_principal": 20,
    "login_ip": 20,
    "device_ip": 20,
    "invite_principal": 30,
    "invite_ip": 20,
    "agent_issuance_principal": 20,
}

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Streams: bounded by connection limits (#115), not by request counts.
_EXEMPT = re.compile(r"/events/stream$|/subscriptions/[^/]+/events$")
_UPLOAD = re.compile(r"/tickets/[^/]+/attachments/?$")
_INVITE = re.compile(r"/workspaces/[^/]+/members/?$")
_AGENT = re.compile(r"/workspaces/[^/]+/agents/?$")

#: (method, path regex, rule) for entry points that have no principal yet.
IP_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("GET", re.compile(r"^/auth/login$"), "login_ip"),
    ("GET", re.compile(r"^/auth/callback$"), "login_ip"),
    ("POST", re.compile(r"^/auth/local/login$"), "login_ip"),
    ("POST", re.compile(r"^/auth/login/password$"), "login_ip"),
    ("POST", re.compile(r"^/login$"), "login_ip"),
    ("POST", re.compile(r"^/api/auth/login$"), "login_ip"),
    ("POST", re.compile(r"^/auth/oidc/device/start$"), "login_ip"),
    ("POST", re.compile(r"^/oauth/device_authorization$"), "device_ip"),
    ("GET", re.compile(r"^/oauth/device/request$"), "device_ip"),
    ("POST", re.compile(r"^/oauth/device/approve$"), "device_ip"),
    ("POST", re.compile(r"^/api/v1/workspaces/invites/accept$"), "invite_ip"),
)


class RateLimited(Exception):
    def __init__(self, rule: str, *, limit: int, retry_after: int) -> None:
        self.rule = rule
        self.limit = limit
        self.retry_after = retry_after
        self.scope = rule.rsplit("_", 1)[-1]  # principal | workspace | ip
        super().__init__(f"rate limit exceeded: {rule}")

    def body(self) -> dict[str, Any]:
        return error_body(
            ErrorCode.RATE_LIMITED,
            "Too many requests; retry later.",
            details={"limit": self.limit, "window_seconds": WINDOW_SECONDS, "scope": self.scope},
        )

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.RATE_LIMITED], content=self.body(), headers=self.headers()
        )

    def http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=HTTP_STATUS[ErrorCode.RATE_LIMITED], detail=self.body(), headers=self.headers()
        )


# --- configuration -----------------------------------------------------------------------


def hosted_profile() -> bool:
    return (os.environ.get("AK5_PROFILE") or "local").strip().lower() == "hosted"


@functools.lru_cache(maxsize=16)
def _parse_limits(hosted: bool, raw: str) -> dict[str, int]:
    limits = dict(HOSTED_DEFAULTS) if hosted else {}
    if raw:
        try:
            overrides = json.loads(raw)
        except ValueError:
            logger.warning("Ignoring AK5_RATE_LIMITS: not valid JSON")
            overrides = {}
        if not isinstance(overrides, dict):
            logger.warning("Ignoring AK5_RATE_LIMITS: expected a JSON object")
            overrides = {}
        for name, value in overrides.items():
            if name not in HOSTED_DEFAULTS:
                logger.warning("Ignoring unknown AK5_RATE_LIMITS rule %r", name)
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                logger.warning("Ignoring AK5_RATE_LIMITS[%r]: expected an integer >= 0", name)
            elif value == 0:
                limits.pop(name, None)  # 0 switches the rule off
            else:
                limits[name] = value
    return limits


def active_limits() -> dict[str, int]:
    return _parse_limits(hosted_profile(), (os.environ.get("AK5_RATE_LIMITS") or "").strip())


# --- counting ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    count: int
    retry_after: int


def _upsert_for(dialect: str):
    if dialect == "sqlite":
        return sqlite.insert
    if dialect == "postgresql":
        return postgresql.insert
    raise RuntimeError(f"Rate limiting does not support the {dialect!r} database dialect")


async def hit(db: AsyncSession, key: str, *, now: float | None = None) -> Hit:
    """Count one request for ``key`` in the current window (atomic upsert, own commit)."""
    moment = time.time() if now is None else now
    window_start = int(moment // WINDOW_SECONDS) * WINDOW_SECONDS
    insert = _upsert_for(db.get_bind().dialect.name)
    stmt = (
        insert(RateCounter)
        .values(key=key, window_start=window_start, count=1)
        .on_conflict_do_update(
            index_elements=[RateCounter.key, RateCounter.window_start],
            set_={"count": RateCounter.count + 1},
        )
        .returning(RateCounter.count)
    )
    count = (await db.execute(stmt)).scalar_one()
    if count == 1 and random.random() < 0.05:  # opportunistic cleanup of long-gone windows
        await db.execute(delete(RateCounter).where(RateCounter.window_start < window_start - 2 * WINDOW_SECONDS))
    await db.commit()
    return Hit(count=count, retry_after=max(1, math.ceil(window_start + WINDOW_SECONDS - moment)))


async def enforce(db: AsyncSession, rule: str, subject: str, *, now: float | None = None) -> None:
    """Raise :class:`RateLimited` when ``subject`` is over ``rule``'s limit (no-op if the rule is off)."""
    limit = active_limits().get(rule)
    if limit is None:
        return
    result = await hit(db, f"{rule}:{subject}", now=now)
    if result.count > limit:
        safe_inc(rate_limited_total, rule=rule)
        raise RateLimited(rule, limit=limit, retry_after=result.retry_after)


async def enforce_authenticated(
    db: AsyncSession, request: Request, *, principal_id: str, workspace_id: str
) -> None:
    """Per-principal / per-workspace limits for a resolved caller (called from the auth dependency)."""
    if not active_limits():
        return
    method, path = request.method.upper(), request.url.path
    if _EXEMPT.search(path):
        return
    if method in _SAFE_METHODS:
        await enforce(db, "read_principal", principal_id)
        return
    await enforce(db, "write_principal", principal_id)
    await enforce(db, "write_workspace", workspace_id)
    if method == "POST" and _UPLOAD.search(path):
        await enforce(db, "upload_principal", principal_id)
    if method == "POST" and _INVITE.search(path):
        await enforce(db, "invite_principal", principal_id)
    if method == "POST" and _AGENT.search(path):
        await enforce(db, "agent_issuance_principal", principal_id)


# --- unauthenticated entry points ----------------------------------------------------------


class RateLimitMiddleware:
    """Per-IP limits for login / device-grant / invite-accept. Passes straight through when off."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not active_limits():
            await self.app(scope, receive, send)
            return
        rule = self._rule_for(scope)
        if rule is None or rule not in active_limits():
            await self.app(scope, receive, send)
            return
        try:
            async with self._session(scope) as db:
                await enforce(db, rule, _client_ip(scope))
        except RateLimited as exc:
            await exc.response()(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _rule_for(scope: Scope) -> str | None:
        method, path = scope.get("method", ""), scope.get("path", "")
        for rule_method, pattern, rule in IP_RULES:
            if method == rule_method and pattern.match(path):
                return rule
        return None

    @staticmethod
    def _session(scope: Scope):
        app = scope.get("app")
        factory = getattr(getattr(app, "state", None), "session_factory", None)
        if factory is None:
            from ak5.server.database import AsyncSessionLocal

            factory = AsyncSessionLocal
        return factory()


def _client_ip(scope: Scope) -> str:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    client = scope.get("client")
    return client_ip_from_headers(
        headers.get("x-forwarded-for"), headers.get("x-real-ip"), client[0] if client else "127.0.0.1"
    )
