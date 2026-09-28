"""Optional web-dashboard gate: username/password + opaque session cookies.

Session cookies are random high-entropy tokens (not password digests). Password
verification and cookie checks use ``secrets.compare_digest`` (timing-safe).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from dataclasses import dataclass

from ak5.config import settings

COOKIE_NAME = "ak5_auth"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 5 * 60
BLOCK_SECONDS = 10 * 60


@dataclass(frozen=True)
class WebAuthConfig:
    enabled: bool
    username: str
    password: str


@dataclass
class _AttemptRecord:
    count: int
    first_attempt: float
    blocked_until: float


_attempts: dict[str, _AttemptRecord] = {}
# token -> unix expiry
_sessions: dict[str, float] = {}
_lock = threading.Lock()


def get_web_auth_config() -> WebAuthConfig:
    password = (
        os.environ.get("AK5_AUTH_PASSWORD")
        or os.environ.get("AK5_WEB_PASSWORD")
        or settings.AUTH_PASSWORD
        or settings.WEB_PASSWORD
        or ""
    ).strip()
    username = (
        os.environ.get("AK5_AUTH_USERNAME")
        or os.environ.get("AK5_WEB_USER")
        or settings.AUTH_USERNAME
        or settings.WEB_USER
        or "admin"
    ).strip() or "admin"
    return WebAuthConfig(enabled=bool(password), username=username, password=password)


def credentials_match(username: str, password: str) -> bool:
    """Timing-safe credential check against configured web auth.

    Digests are compared so unequal username/password lengths never raise
    ``ValueError`` from ``secrets.compare_digest`` (would become HTTP 500).
    """
    cfg = get_web_auth_config()
    if not cfg.enabled:
        return False
    user_ok = secrets.compare_digest(
        hashlib.sha256(username.encode("utf-8")).digest(),
        hashlib.sha256(cfg.username.encode("utf-8")).digest(),
    )
    pass_ok = secrets.compare_digest(
        hashlib.sha256(password.encode("utf-8")).digest(),
        hashlib.sha256(cfg.password.encode("utf-8")).digest(),
    )
    return user_ok and pass_ok


def issue_session_token() -> str:
    """Mint an opaque session token after successful login (server-side store)."""
    token = secrets.token_urlsafe(48)
    expires = time.time() + SESSION_MAX_AGE_SECONDS
    with _lock:
        _prune_sessions_locked(now=time.time())
        _sessions[token] = expires
    return token


def revoke_session_token(token: str | None) -> None:
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def _prune_sessions_locked(*, now: float) -> None:
    expired = [t for t, exp in _sessions.items() if exp <= now]
    for t in expired:
        del _sessions[t]


def verify_session_cookie(cookie_value: str | None) -> bool:
    cfg = get_web_auth_config()
    if not cfg.enabled:
        return True
    if not cookie_value:
        return False
    now = time.time()
    with _lock:
        _prune_sessions_locked(now=now)
        expires = _sessions.get(cookie_value)
        if expires is None or expires <= now:
            # Timing-safe miss: compare against a dummy so length still matters less.
            secrets.compare_digest(cookie_value, cookie_value)
            return False
        # Confirm membership with compare_digest against the stored key.
        for token, exp in _sessions.items():
            if exp <= now:
                continue
            if secrets.compare_digest(cookie_value, token):
                return True
        return False


def check_rate_limit(ip: str) -> tuple[bool, int | None]:
    """Return (allowed, wait_seconds_if_blocked)."""
    now = time.monotonic()
    with _lock:
        record = _attempts.get(ip)
        if not record:
            return True, None
        if record.blocked_until > now:
            # Extend lockout while still hammering (M-8).
            record.blocked_until = now + BLOCK_SECONDS
            return False, max(1, int(record.blocked_until - now))
        if now - record.first_attempt > WINDOW_SECONDS:
            del _attempts[ip]
            return True, None
        if record.count >= MAX_ATTEMPTS:
            record.blocked_until = now + BLOCK_SECONDS
            return False, BLOCK_SECONDS
        return True, None


def record_failed_attempt(ip: str) -> None:
    now = time.monotonic()
    with _lock:
        record = _attempts.get(ip)
        if not record or now - record.first_attempt > WINDOW_SECONDS:
            _attempts[ip] = _AttemptRecord(count=1, first_attempt=now, blocked_until=0.0)
            return
        record.count += 1
        if record.count >= MAX_ATTEMPTS:
            record.blocked_until = now + BLOCK_SECONDS


def record_successful_attempt(ip: str) -> None:
    with _lock:
        _attempts.pop(ip, None)


def client_ip_from_headers(x_forwarded_for: str | None, x_real_ip: str | None, fallback: str) -> str:
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip() or fallback
    if x_real_ip:
        return x_real_ip.strip() or fallback
    return fallback


def reset_rate_limits_for_tests() -> None:
    with _lock:
        _attempts.clear()
        _sessions.clear()


# Back-compat alias used by older tests — prefer issue_session_token().
def compute_session_hash(username: str, password: str) -> str:
    """Deprecated: issues a real session only if credentials match."""
    if not credentials_match(username, password):
        raise ValueError("Invalid credentials for session mint")
    return issue_session_token()
