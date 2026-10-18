"""AK5 OAuth device authorization grant (identity.md §3.3, RFC 8628)."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.server.models.oauth_device import OAuthDeviceCode
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID

CODE_EXPIRE_MINUTES = 10
POLL_INTERVAL_SECONDS = 5
USER_CODE_MAX_ATTEMPTS = 10
CLI_CLIENT_ID = "ak5-cli"


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


def generate_user_code() -> str:
    """Human-friendly code like ``AK5-7X2M`` (omit 0/1/O/I)."""
    chars = string.ascii_uppercase + "23456789"
    suffix = "".join(secrets.choice(chars) for _ in range(4))
    return f"AK5-{suffix}"


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def start_user_device_authorization(
    db: AsyncSession,
    *,
    client_id: str,
) -> DeviceAuthorizationStarted:
    if client_id != CLI_CLIENT_ID:
        raise DeviceGrantError("invalid_client")

    device_code = secrets.token_urlsafe(32)
    user_code = generate_user_code()
    for _ in range(8):
        existing = (
            await db.execute(select(OAuthDeviceCode).where(OAuthDeviceCode.user_code == user_code))
        ).scalar_one_or_none()
        if existing is None:
            break
        user_code = generate_user_code()

    expires_at = _now_naive() + timedelta(minutes=CODE_EXPIRE_MINUTES)
    db.add(
        OAuthDeviceCode(
            device_code=device_code,
            user_code=user_code,
            client_id=client_id,
            principal="user",
            status="pending",
            workspace_id=None,
            approved_by_user_id=None,
            requested_capabilities="[]",
            interval_seconds=POLL_INTERVAL_SECONDS,
            last_poll_at=None,
            user_code_attempts=0,
            expires_at=expires_at,
        )
    )
    await db.flush()
    return DeviceAuthorizationStarted(
        device_code=device_code,
        user_code=user_code,
        expires_in=CODE_EXPIRE_MINUTES * 60,
        interval=POLL_INTERVAL_SECONDS,
    )


async def _get_by_user_code(db: AsyncSession, user_code: str) -> OAuthDeviceCode | None:
    normalized = user_code.strip().upper()
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


async def approve_or_deny_user_device(
    db: AsyncSession,
    *,
    user_code: str,
    approver_user_id: str,
    approved: bool,
    workspace_id: str | None = None,
) -> OAuthDeviceCode:
    row = await lookup_device_by_user_code(db, user_code)
    if row is None:
        raise DeviceGrantError("invalid_request")
    if row.principal != "user":
        raise DeviceGrantError("invalid_request")
    if row.status != "pending":
        raise DeviceGrantError("invalid_grant")
    if row.expires_at <= _now_naive():
        row.status = "expired"
        await db.flush()
        raise DeviceGrantError("expired_token")

    row.user_code_attempts += 1
    if row.user_code_attempts > USER_CODE_MAX_ATTEMPTS:
        row.status = "denied"
        await db.flush()
        raise DeviceGrantError("access_denied")

    if not approved:
        row.status = "denied"
        row.approved_by_user_id = approver_user_id
        await db.flush()
        return row

    row.status = "approved"
    row.approved_by_user_id = approver_user_id
    row.workspace_id = workspace_id or LOCAL_WORKSPACE_ID
    await db.flush()
    return row


async def poll_user_device_token(
    db: AsyncSession,
    *,
    device_code: str,
    client_id: str,
) -> OAuthDeviceCode:
    """Return an approved row ready for token minting, or raise DeviceGrantError."""
    if client_id != CLI_CLIENT_ID:
        raise DeviceGrantError("invalid_client")

    row = await db.get(OAuthDeviceCode, device_code)
    if row is None:
        raise DeviceGrantError("invalid_grant")
    if row.client_id != client_id:
        raise DeviceGrantError("invalid_client")
    if row.principal != "user":
        raise DeviceGrantError("invalid_grant")

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

    # Enforce poll interval only while still pending (RFC 8628 slow_down).
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
