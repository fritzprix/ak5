"""Web user sessions + CSRF (identity.md §3.2)."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from ak5.server.models.workspace import UserSession

SESSION_COOKIE = "ak5_session"
CSRF_COOKIE = "ak5_csrf"
CSRF_HEADER = "X-CSRF-Token"
# Binds OIDC `state` to the browser that started login (login CSRF / RFC 6749 §10.12).
OIDC_STATE_COOKIE = "ak5_oidc_state"
SESSION_TTL = timedelta(days=14)
CLI_SESSION_TTL = timedelta(hours=12)
OIDC_PENDING_TTL = timedelta(minutes=10)
OIDC_PENDING_MAX = 256
OIDC_STATE_COOKIE_MAX_AGE = int(OIDC_PENDING_TTL.total_seconds())


class _OidcPending(TypedDict):
    provider_id: str
    nonce: str
    code_verifier: str
    expires_at: float


# In-process pending OIDC login state (state → payload). TTL + size capped.
_oidc_pending: dict[str, _OidcPending] = {}
_oidc_lock = threading.Lock()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _purge_oidc_pending_locked(now: float) -> None:
    expired = [key for key, meta in _oidc_pending.items() if meta["expires_at"] <= now]
    for key in expired:
        del _oidc_pending[key]


def _evict_oldest_oidc_pending_locked() -> None:
    if not _oidc_pending:
        return
    oldest_key = min(_oidc_pending.items(), key=lambda item: item[1]["expires_at"])[0]
    del _oidc_pending[oldest_key]


def store_oidc_pending(
    *,
    state: str,
    provider_id: str,
    nonce: str,
    code_verifier: str,
) -> None:
    now = time.time()
    with _oidc_lock:
        _purge_oidc_pending_locked(now)
        while len(_oidc_pending) >= OIDC_PENDING_MAX:
            _evict_oldest_oidc_pending_locked()
        _oidc_pending[state] = {
            "provider_id": provider_id,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "expires_at": now + OIDC_PENDING_TTL.total_seconds(),
        }


def pop_oidc_pending(state: str) -> dict[str, str] | None:
    now = time.time()
    with _oidc_lock:
        _purge_oidc_pending_locked(now)
        meta = _oidc_pending.pop(state, None)
        if meta is None:
            return None
        if meta["expires_at"] <= now:
            return None
        return {
            "provider_id": meta["provider_id"],
            "nonce": meta["nonce"],
            "code_verifier": meta["code_verifier"],
        }


def oidc_state_matches(cookie_state: str | None, query_state: str) -> bool:
    """Constant-time compare of browser-bound state cookie vs callback query."""
    if not cookie_state or not query_state:
        return False
    return secrets.compare_digest(cookie_state, query_state)


def oidc_pending_count_for_tests() -> int:
    with _oidc_lock:
        return len(_oidc_pending)


def inject_expired_oidc_pending_for_tests(
    *,
    state: str,
    provider_id: str = "mock",
    nonce: str = "n",
    code_verifier: str = "verifier",
) -> None:
    """Test helper: insert a pending entry that is already past TTL."""
    with _oidc_lock:
        _oidc_pending[state] = {
            "provider_id": provider_id,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "expires_at": time.time() - 1.0,
        }


def clear_oidc_pending_for_tests() -> None:
    with _oidc_lock:
        _oidc_pending.clear()


async def create_web_session_tokens(db: AsyncSession, user_id: str) -> tuple[str, str]:
    """Return ``(session_cookie_value, csrf_token)`` after persisting the session."""
    session_id = secrets.token_urlsafe(24)
    cookie_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC).replace(tzinfo=None) + SESSION_TTL
    db.add(
        UserSession(
            session_id=session_id,
            user_id=user_id,
            refresh_token_hash=f"{_hash_token(cookie_token)}:{_hash_token(csrf_token)}",
            client="web",
            expires_at=expires,
            revoked_at=None,
        )
    )
    await db.flush()
    return f"{session_id}.{cookie_token}", csrf_token


async def create_cli_session_tokens(db: AsyncSession, user_id: str) -> tuple[str, str]:
    """Return ``(refresh_token, session_id)`` for a CLI device-grant session (§3.3)."""
    session_id = secrets.token_urlsafe(24)
    refresh_token = secrets.token_urlsafe(48)
    expires = datetime.now(UTC).replace(tzinfo=None) + CLI_SESSION_TTL
    db.add(
        UserSession(
            session_id=session_id,
            user_id=user_id,
            refresh_token_hash=_hash_token(refresh_token),
            client="cli",
            expires_at=expires,
            revoked_at=None,
        )
    )
    await db.flush()
    return refresh_token, session_id


async def revoke_all_cli_sessions(db: AsyncSession, user_id: str) -> None:
    """Revoke every CLI session for ``user_id`` (refresh reuse detection)."""
    from sqlalchemy import select

    now = datetime.now(UTC).replace(tzinfo=None)
    rows = (
        await db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id,
                UserSession.client == "cli",
                UserSession.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for row in rows:
        row.revoked_at = now
    await db.flush()


async def rotate_cli_refresh_token(
    db: AsyncSession, refresh_token: str
) -> tuple[str, str] | None:
    """Rotate a CLI refresh token.

    Returns ``(new_refresh_token, user_id)`` on success.
    On reuse of a revoked token, revokes all CLI sessions for that user and
    returns ``None``. Unknown / expired tokens also return ``None``.
    """
    from sqlalchemy import select

    token_hash = _hash_token(refresh_token)
    row = (
        await db.execute(
            select(UserSession).where(
                UserSession.refresh_token_hash == token_hash,
                UserSession.client == "cli",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    if row.revoked_at is not None:
        await revoke_all_cli_sessions(db, row.user_id)
        return None
    if row.expires_at <= now:
        row.revoked_at = now
        await db.flush()
        return None
    row.revoked_at = now
    new_refresh, _session_id = await create_cli_session_tokens(db, row.user_id)
    return new_refresh, row.user_id


async def revoke_cli_refresh_token(db: AsyncSession, refresh_token: str) -> bool:
    """RFC 7009-style revoke for a CLI refresh token. Returns True if found."""
    from sqlalchemy import select

    token_hash = _hash_token(refresh_token)
    row = (
        await db.execute(
            select(UserSession).where(
                UserSession.refresh_token_hash == token_hash,
                UserSession.client == "cli",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
    return True


async def revoke_agent_credential_by_token(db: AsyncSession, token: str) -> bool:
    """Revoke an ``ak5_agt_…`` credential by plaintext token. Returns True if found."""
    from sqlalchemy import select

    from ak5.server.models.workspace import AgentCredential

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = (
        await db.execute(
            select(AgentCredential).where(AgentCredential.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
    return True


async def resolve_web_session(
    db: AsyncSession, cookie_value: str | None
) -> tuple[str, str] | None:
    """Return ``(user_id, csrf_hash)`` when the session cookie is valid."""
    if not cookie_value or "." not in cookie_value:
        return None
    session_id, cookie_token = cookie_value.split(".", 1)
    row = await db.get(UserSession, session_id)
    if row is None or row.client != "web" or row.revoked_at is not None:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    if row.expires_at <= now:
        return None
    parts = row.refresh_token_hash.split(":", 1)
    if len(parts) != 2:
        return None
    cookie_hash, csrf_hash = parts
    if not secrets.compare_digest(cookie_hash, _hash_token(cookie_token)):
        return None
    return row.user_id, csrf_hash


async def revoke_web_session(db: AsyncSession, cookie_value: str | None) -> None:
    if not cookie_value or "." not in cookie_value:
        return
    session_id = cookie_value.split(".", 1)[0]
    row = await db.get(UserSession, session_id)
    if row is None:
        return
    row.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()


def csrf_matches(presented: str | None, csrf_hash: str) -> bool:
    if not presented:
        return False
    return secrets.compare_digest(_hash_token(presented), csrf_hash)


def require_csrf_header(headers: Mapping[str, str], csrf_hash: str) -> bool:
    """Double-submit CSRF: validate ``X-CSRF-Token`` header only (never the cookie)."""
    return csrf_matches(headers.get(CSRF_HEADER), csrf_hash)
