"""Optional web-dashboard gate: username/password cookie + brute-force shield.

Mirrors the Next.js auth contract (cookie name, hash salt, rate limits) so the
embedded static UI and `next dev` proxy share the same session semantics.
"""

from __future__ import annotations

import hashlib
import os
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


def compute_session_hash(username: str, password: str) -> str:
    payload = f"ak5_salt_{username}_{password}_2026".encode()
    return hashlib.sha256(payload).hexdigest()


def verify_session_cookie(cookie_value: str | None) -> bool:
    cfg = get_web_auth_config()
    if not cfg.enabled:
        return True
    if not cookie_value:
        return False
    expected = compute_session_hash(cfg.username, cfg.password)
    return cookie_value == expected


def check_rate_limit(ip: str) -> tuple[bool, int | None]:
    """Return (allowed, wait_seconds_if_blocked)."""
    now = time.monotonic()
    with _lock:
        record = _attempts.get(ip)
        if not record:
            return True, None
        if record.blocked_until > now:
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
