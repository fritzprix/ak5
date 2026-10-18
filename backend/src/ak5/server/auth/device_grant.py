"""AK5 OAuth device authorization grant (identity.md §3.3 / §4.1, RFC 8628)."""

from __future__ import annotations

import hashlib
import json
import secrets
import string
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.stages import Role
from ak5.server.models.actor import Actor
from ak5.server.models.oauth_device import OAuthDeviceCode
from ak5.server.models.workspace import (
    LOCAL_WORKSPACE_ID,
    Agent,
    AgentCredential,
    Membership,
)

CODE_EXPIRE_MINUTES = 10
POLL_INTERVAL_SECONDS = 5
CLI_CLIENT_ID = "ak5-cli"
# XXXX-XXXX from 32-char alphabet ≈ 40 bits (RFC 8628 §5.1 + rate limit).
_USER_CODE_ALPHABET = string.ascii_uppercase + "23456789"
_RATE_WINDOW_SECONDS = 600.0
_RATE_MAX_FAILURES = 20
_rate_failures: dict[str, list[float]] = {}
_rate_lock = threading.Lock()

_AGENT_ROLES = frozenset({Role.TRIAGE.value, Role.WORKER.value, Role.GATEKEEPER.value})
_ADMIN_MEMBERSHIP_ROLES = frozenset({"owner", "admin"})


class DeviceGrantError(Exception):
    """OAuth device-grant error with RFC-style ``error`` code."""

    def __init__(self, error: str, *, retry_after: int | None = None) -> None:
        self.error = error
        self.retry_after = retry_after
        super().__init__(error)


@dataclass(frozen=True)
class DeviceAuthorizationStarted:
    device_code: str
    user_code: str
    expires_in: int
    interval: int


def hash_device_code(device_code: str) -> str:
    return hashlib.sha256(device_code.encode("utf-8")).hexdigest()


def generate_user_code() -> str:
    """Human-friendly ``XXXX-XXXX`` (omit 0/1/O/I)."""
    chars = _USER_CODE_ALPHABET
    left = "".join(secrets.choice(chars) for _ in range(4))
    right = "".join(secrets.choice(chars) for _ in range(4))
    return f"{left}-{right}"


def normalize_user_code(user_code: str) -> str:
    return user_code.strip().upper().replace(" ", "")


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def check_user_code_rate_limit(client_key: str) -> None:
    """Raise ``slow_down`` when ``client_key`` (IP) exceeds failed lookup/approve budget."""
    now = time.time()
    with _rate_lock:
        stamps = [t for t in _rate_failures.get(client_key, []) if now - t < _RATE_WINDOW_SECONDS]
        _rate_failures[client_key] = stamps
        if len(stamps) >= _RATE_MAX_FAILURES:
            raise DeviceGrantError("slow_down", retry_after=int(_RATE_WINDOW_SECONDS))


def record_user_code_failure(client_key: str) -> None:
    now = time.time()
    with _rate_lock:
        stamps = [t for t in _rate_failures.get(client_key, []) if now - t < _RATE_WINDOW_SECONDS]
        stamps.append(now)
        _rate_failures[client_key] = stamps


def clear_user_code_rate_limits_for_tests() -> None:
    with _rate_lock:
        _rate_failures.clear()


async def _allocate_user_code(db: AsyncSession) -> str:
    user_code = generate_user_code()
    for _ in range(8):
        existing = (
            await db.execute(
                select(OAuthDeviceCode).where(OAuthDeviceCode.user_code == user_code)
            )
        ).scalar_one_or_none()
        if existing is None:
            return user_code
        user_code = generate_user_code()
    return user_code


async def start_device_authorization(
    db: AsyncSession,
    *,
    client_id: str,
    principal: Literal["user", "agent"],
    name: str | None = None,
    workflow_role: str | None = None,
    capabilities: list[str] | None = None,
    workspace_id: str | None = None,
) -> DeviceAuthorizationStarted:
    if client_id != CLI_CLIENT_ID:
        raise DeviceGrantError("invalid_client")
    if principal == "agent":
        role = (workflow_role or Role.WORKER.value).lower()
        if role not in _AGENT_ROLES:
            raise DeviceGrantError("invalid_request")
        if not name or not name.strip():
            raise DeviceGrantError("invalid_request")

    plaintext_device = secrets.token_urlsafe(32)
    user_code = await _allocate_user_code(db)
    expires_at = _now_naive() + timedelta(minutes=CODE_EXPIRE_MINUTES)
    db.add(
        OAuthDeviceCode(
            device_code=hash_device_code(plaintext_device),
            user_code=user_code,
            client_id=client_id,
            principal=principal,
            status="pending",
            workspace_id=workspace_id,
            approved_by_user_id=None,
            requested_name=name.strip() if name else None,
            requested_role=(workflow_role or Role.WORKER.value).lower()
            if principal == "agent"
            else None,
            requested_capabilities=json.dumps(capabilities or []),
            requested_board_scope="[]",
            interval_seconds=POLL_INTERVAL_SECONDS,
            last_poll_at=None,
            user_code_attempts=0,
            expires_at=expires_at,
        )
    )
    await db.flush()
    return DeviceAuthorizationStarted(
        device_code=plaintext_device,
        user_code=user_code,
        expires_in=CODE_EXPIRE_MINUTES * 60,
        interval=POLL_INTERVAL_SECONDS,
    )


async def _get_by_user_code(db: AsyncSession, user_code: str) -> OAuthDeviceCode | None:
    normalized = normalize_user_code(user_code)
    return (
        await db.execute(select(OAuthDeviceCode).where(OAuthDeviceCode.user_code == normalized))
    ).scalar_one_or_none()


async def lookup_device_by_user_code(
    db: AsyncSession, user_code: str
) -> OAuthDeviceCode | None:
    row = await _get_by_user_code(db, user_code)
    if row is None:
        return None
    if row.status == "pending" and row.expires_at <= _now_naive():
        row.status = "expired"
        await db.flush()
    return row


async def _require_admin_membership(
    db: AsyncSession, *, user_id: str, workspace_id: str
) -> Membership:
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None or membership.role not in _ADMIN_MEMBERSHIP_ROLES:
        raise DeviceGrantError("access_denied")
    return membership


async def approve_or_deny_device(
    db: AsyncSession,
    *,
    user_code: str,
    approver_user_id: str,
    approved: bool,
    workspace_id: str | None = None,
    workflow_role: str | None = None,
    capabilities: list[str] | None = None,
    board_scope: list[str] | None = None,
    client_key: str = "unknown",
) -> OAuthDeviceCode:
    check_user_code_rate_limit(client_key)
    row = await lookup_device_by_user_code(db, user_code)
    if row is None:
        record_user_code_failure(client_key)
        raise DeviceGrantError("invalid_request")
    if row.status != "pending":
        record_user_code_failure(client_key)
        raise DeviceGrantError("invalid_grant")
    if row.expires_at <= _now_naive():
        row.status = "expired"
        await db.flush()
        raise DeviceGrantError("expired_token")

    if not approved:
        row.status = "denied"
        row.approved_by_user_id = approver_user_id
        await db.flush()
        return row

    target_ws = workspace_id or row.workspace_id or LOCAL_WORKSPACE_ID
    if row.principal == "agent":
        await _require_admin_membership(db, user_id=approver_user_id, workspace_id=target_ws)
        role = (workflow_role or row.requested_role or Role.WORKER.value).lower()
        if role not in _AGENT_ROLES:
            raise DeviceGrantError("invalid_request")
        row.requested_role = role
        if capabilities is not None:
            row.requested_capabilities = json.dumps(capabilities)
        if board_scope is not None:
            row.requested_board_scope = json.dumps(board_scope)
    else:
        # User principal: any authenticated member of the workspace may approve.
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.workspace_id == target_ws,
                    Membership.user_id == approver_user_id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise DeviceGrantError("access_denied")

    row.status = "approved"
    row.approved_by_user_id = approver_user_id
    row.workspace_id = target_ws
    await db.flush()
    return row


async def poll_device_token(
    db: AsyncSession,
    *,
    device_code: str,
    client_id: str,
) -> OAuthDeviceCode:
    """Return an approved row ready for token minting, or raise DeviceGrantError."""
    if client_id != CLI_CLIENT_ID:
        raise DeviceGrantError("invalid_client")

    row = await db.get(OAuthDeviceCode, hash_device_code(device_code))
    if row is None:
        raise DeviceGrantError("invalid_grant")
    if row.client_id != client_id:
        raise DeviceGrantError("invalid_client")

    now = _now_naive()
    if row.status == "pending" and row.expires_at <= now:
        row.status = "expired"
        await db.flush()
        raise DeviceGrantError("expired_token")
    if row.status == "expired":
        raise DeviceGrantError("expired_token")
    if row.status == "denied":
        raise DeviceGrantError("access_denied")
    if row.status == "consumed":
        raise DeviceGrantError("invalid_grant")

    interval = int(row.interval_seconds or POLL_INTERVAL_SECONDS)
    if row.status == "approved":
        return row

    if row.last_poll_at is not None:
        elapsed = (now - row.last_poll_at).total_seconds()
        if elapsed < interval:
            raise DeviceGrantError("slow_down", retry_after=interval)
    row.last_poll_at = now
    await db.flush()

    if row.status == "pending":
        raise DeviceGrantError("authorization_pending", retry_after=interval)
    raise DeviceGrantError("invalid_grant")


async def mark_device_consumed(db: AsyncSession, row: OAuthDeviceCode) -> None:
    row.status = "consumed"
    await db.flush()


async def mint_agent_from_approved_device(
    db: AsyncSession,
    row: OAuthDeviceCode,
) -> tuple[Agent, str]:
    """Create Agent + AgentCredential; return ``(agent, plaintext_token)``."""
    if row.principal != "agent" or not row.approved_by_user_id:
        raise DeviceGrantError("invalid_grant")
    workspace_id = row.workspace_id or LOCAL_WORKSPACE_ID
    agent_id = f"agt_{uuid.uuid4().hex[:16]}"
    caps = row.requested_capabilities or "[]"
    scope = row.requested_board_scope or "[]"
    name = row.requested_name or agent_id
    workflow_role = row.requested_role or Role.WORKER.value
    agent = Agent(
        agent_id=agent_id,
        workspace_id=workspace_id,
        name=name,
        workflow_role=workflow_role,
        capabilities=caps,
        board_scope=scope,
        created_by=row.approved_by_user_id,
        disabled_at=None,
    )
    db.add(agent)
    # Principal-id Actor row so Bearer auth / FKs resolve (identity.md §9 / #23).
    db.add(
        Actor(
            actor_id=agent_id,
            actor_type="agent",
            name=name,
            role=workflow_role,
            capabilities=caps,
            status="idle",
        )
    )
    plaintext = f"ak5_agt_{secrets.token_urlsafe(32)}"
    cred = AgentCredential(
        credential_id=f"cred_{uuid.uuid4().hex[:16]}",
        agent_id=agent_id,
        token_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
        board_scope=scope,
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
    )
    db.add(cred)
    await db.flush()
    return agent, plaintext


def parse_capabilities(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]
