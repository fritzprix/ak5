"""DB-backed pending auth state (deployment.md §6, #107).

Replaces the per-process dicts that broke with more than one instance (a callback landing
on another instance failed) and let an attacker push a real user's login out by filling a
256-slot table. Here:

* every instance reads the same rows;
* entries expire by TTL, never by eviction;
* each client key (IP) may hold at most ``PENDING_PER_CLIENT_MAX`` live entries, so one
  caller can only exhaust its own budget (``PendingLimitError``).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.server.models.auth_state import DevicePending, OidcPending

OIDC_PENDING_TTL = timedelta(minutes=10)
PENDING_PER_CLIENT_MAX = 20


class PendingLimitError(Exception):
    """This client key already has the maximum number of live pending entries."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("too_many_pending")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def hash_device_code(device_code: str) -> str:
    return hashlib.sha256(device_code.encode("utf-8")).hexdigest()


# --- OIDC login (authorization code + PKCE) ---------------------------------------------


async def store_oidc_pending(
    db: AsyncSession,
    *,
    state: str,
    provider_id: str,
    nonce: str,
    code_verifier: str,
    client_key: str,
) -> None:
    now = _now()
    await db.execute(delete(OidcPending).where(OidcPending.expires_at <= now))
    live = (
        await db.execute(
            select(func.count()).select_from(OidcPending).where(OidcPending.client_key == client_key)
        )
    ).scalar_one()
    if live >= PENDING_PER_CLIENT_MAX:
        await db.commit()
        raise PendingLimitError(int(OIDC_PENDING_TTL.total_seconds()))
    db.add(
        OidcPending(
            state=state,
            provider_id=provider_id,
            nonce=nonce,
            code_verifier=code_verifier,
            client_key=client_key,
            expires_at=now + OIDC_PENDING_TTL,
        )
    )
    await db.commit()


async def pop_oidc_pending(db: AsyncSession, state: str) -> dict[str, str] | None:
    """Consume ``state`` exactly once (atomic ``DELETE … RETURNING``), even across instances."""
    result = await db.execute(
        delete(OidcPending)
        .where(OidcPending.state == state)
        .returning(
            OidcPending.provider_id,
            OidcPending.nonce,
            OidcPending.code_verifier,
            OidcPending.expires_at,
        )
    )
    row = result.first()
    await db.commit()
    if row is None or row.expires_at <= _now():
        return None
    return {
        "provider_id": row.provider_id,
        "nonce": row.nonce,
        "code_verifier": row.code_verifier,
    }


async def count_oidc_pending(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(OidcPending))).scalar_one()


async def inject_expired_oidc_pending_for_tests(
    db: AsyncSession,
    *,
    state: str,
    provider_id: str = "mock",
    nonce: str = "n",
    code_verifier: str = "verifier",
    client_key: str = "test",
) -> None:
    """Test helper: insert a pending entry that is already past TTL."""
    db.add(
        OidcPending(
            state=state,
            provider_id=provider_id,
            nonce=nonce,
            code_verifier=code_verifier,
            client_key=client_key,
            expires_at=_now() - timedelta(seconds=1),
        )
    )
    await db.commit()


# --- Upstream IdP device flow -------------------------------------------------------------


async def store_device_pending(
    db: AsyncSession,
    *,
    device_code: str,
    provider_id: str,
    interval: int,
    expires_in: int,
    client_key: str,
) -> None:
    now = _now()
    await db.execute(delete(DevicePending).where(DevicePending.expires_at <= now))
    live = (
        await db.execute(
            select(func.count()).select_from(DevicePending).where(DevicePending.client_key == client_key)
        )
    ).scalar_one()
    if live >= PENDING_PER_CLIENT_MAX:
        await db.commit()
        raise PendingLimitError(max(interval, 5))
    db.add(
        DevicePending(
            device_code_hash=hash_device_code(device_code),
            provider_id=provider_id,
            interval=interval,
            last_poll=0.0,
            client_key=client_key,
            expires_at=now + timedelta(seconds=expires_in),
        )
    )
    await db.commit()


async def load_device_pending(db: AsyncSession, device_code: str) -> DevicePending | None:
    """The live entry for ``device_code`` (expired rows count as missing and are removed)."""
    row = await db.get(DevicePending, hash_device_code(device_code))
    if row is None:
        return None
    if row.expires_at <= _now():
        await db.delete(row)
        await db.commit()
        return None
    return row


async def claim_device_poll(db: AsyncSession, device_code: str, *, interval: int) -> bool:
    """Atomically take the next poll slot; False means "slow_down" (polled too recently).

    The conditional UPDATE is the lock, so two instances polling the same code cannot both win.
    """
    now = datetime.now(UTC).timestamp()
    result = await db.execute(
        update(DevicePending)
        .where(
            DevicePending.device_code_hash == hash_device_code(device_code),
            DevicePending.last_poll <= now - interval,
        )
        .values(last_poll=now)
    )
    await db.commit()
    return bool(result.rowcount)


async def set_device_interval(db: AsyncSession, device_code: str, interval: int) -> None:
    await db.execute(
        update(DevicePending)
        .where(DevicePending.device_code_hash == hash_device_code(device_code))
        .values(interval=interval)
    )
    await db.commit()


async def delete_device_pending(db: AsyncSession, device_code: str) -> None:
    await db.execute(
        delete(DevicePending).where(DevicePending.device_code_hash == hash_device_code(device_code))
    )
    await db.commit()


async def count_device_pending(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(DevicePending))).scalar_one()


async def mint_local_device_code_for_tests(
    db: AsyncSession, provider_id: str, *, interval: int = 1, client_key: str = "test"
) -> str:
    """Test helper: register a synthetic pending device_code."""
    import secrets

    code = secrets.token_urlsafe(16)
    await store_device_pending(
        db,
        device_code=code,
        provider_id=provider_id,
        interval=interval,
        expires_in=600,
        client_key=client_key,
    )
    return code

