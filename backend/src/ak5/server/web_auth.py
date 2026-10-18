"""Local-owner password helpers + login rate limits (identity.md §8).

Web sessions live in ``user_sessions`` via ``ak5.server.auth.sessions`` —
this module no longer keeps an in-memory session store.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from dataclasses import dataclass

from ak5.server.config import settings

# Legacy cookie name kept for clearing old browsers; gate uses ``ak5_session``.
COOKIE_NAME = "ak5_auth"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
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
    """Timing-safe credential check against configured local-owner password."""
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
    """Deprecated: sessions are DB-backed. Returns empty; callers must use auth.sessions."""
    return ""


def revoke_session_token(token: str | None) -> None:
    """Deprecated no-op — revoke via ``auth.sessions.revoke_web_session``."""
    _ = token


def verify_session_cookie(cookie_value: str | None) -> bool:
    """Deprecated: in-memory sessions removed. Always False for legacy ``ak5_auth``."""
    _ = cookie_value
    return False


def check_rate_limit(ip: str) -> tuple[bool, int | None]:
    """Return (allowed, wait_seconds_if_blocked)."""
    now = time.monotonic()
    with _lock:
        record = _attempts.get(ip)
        if not record:
            return True, None
        if record.blocked_until > now:
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


def compute_session_hash(username: str, password: str) -> str:
    """Deprecated compatibility stub — always raises; use ``/auth/local/login``."""
    if not credentials_match(username, password):
        raise ValueError("Invalid credentials for session mint")
    raise RuntimeError(
        "In-memory web sessions removed; use POST /auth/local/login or /api/auth/login"
    )
