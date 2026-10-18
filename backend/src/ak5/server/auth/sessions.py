"""Web user sessions + CSRF (identity.md §3.2)."""

from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ak5.server.models.workspace import UserSession

SESSION_COOKIE = "ak5_session"
CSRF_COOKIE = "ak5_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = timedelta(days=14)

# In-process pending OIDC login state (state → payload).
_oidc_pending: dict[str, dict[str, str]] = {}
_oidc_lock = threading.Lock()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def store_oidc_pending(
    *,
    state: str,
    provider_id: str,
    nonce: str,
    code_verifier: str,
) -> None:
    with _oidc_lock:
        _oidc_pending[state] = {
            "provider_id": provider_id,
            "nonce": nonce,
            "code_verifier": code_verifier,
        }


def pop_oidc_pending(state: str) -> dict[str, str] | None:
    with _oidc_lock:
        return _oidc_pending.pop(state, None)


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
