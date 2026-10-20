"""Local-owner password helpers + login rate limits (identity.md §8).

Web sessions live in ``user_sessions`` via ``ak5.server.auth.sessions`` —
this module no longer keeps an in-memory session store.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass

from ak5.server.config import settings

logger = logging.getLogger(__name__)

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


BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 10 * 60.0
MAX_ATTEMPT_ENTRIES = 1000


def _prune_expired_attempts(now: float) -> None:
    """Evict expired lockout entries and cap in-memory size (#118)."""
    expired = [
        ip
        for ip, rec in _attempts.items()
        if rec.blocked_until <= now and (now - rec.first_attempt) > WINDOW_SECONDS
    ]
    for ip in expired:
        _attempts.pop(ip, None)
    if len(_attempts) > MAX_ATTEMPT_ENTRIES:
        # Evict oldest entries first to stay within bounded capacity
        overflow = len(_attempts) - MAX_ATTEMPT_ENTRIES
        oldest_ips = sorted(_attempts.keys(), key=lambda k: _attempts[k].first_attempt)[:overflow]
        for ip in oldest_ips:
            _attempts.pop(ip, None)


def check_rate_limit(ip: str) -> tuple[bool, int | None]:
    """Return (allowed, wait_seconds_if_blocked)."""
    now = time.monotonic()
    with _lock:
        _prune_expired_attempts(now)
        record = _attempts.get(ip)
        if not record:
            return True, None
        if record.blocked_until > now:
            return False, max(1, int(record.blocked_until - now))
        if now - record.first_attempt > WINDOW_SECONDS and record.blocked_until <= now:
            del _attempts[ip]
            return True, None
        return True, None


def record_failed_attempt(ip: str) -> None:
    now = time.monotonic()
    with _lock:
        _prune_expired_attempts(now)
        record = _attempts.get(ip)
        if not record or (now - record.first_attempt > WINDOW_SECONDS and record.blocked_until <= now):
            _attempts[ip] = _AttemptRecord(count=1, first_attempt=now, blocked_until=0.0)
            return
        record.count += 1
        if record.count >= MAX_ATTEMPTS:
            # Exponential delay for consecutive failures beyond MAX_ATTEMPTS:
            # 5th attempt: 2s, 6th: 4s, 7th: 8s... up to MAX_DELAY_SECONDS (600s).
            delay = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2 ** (record.count - MAX_ATTEMPTS)))
            record.blocked_until = now + delay


def record_successful_attempt(ip: str) -> None:
    with _lock:
        _attempts.pop(ip, None)


# Platform-guaranteed client IP headers (deployment.md / #125). Only enable when
# the platform overwrites the header at the edge — never on open internet hops.
ALLOWED_CLIENT_IP_HEADERS = frozenset(
    {
        "x-real-ip",
        "x-vercel-forwarded-for",
        "cf-connecting-ip",
    }
)


def configured_client_ip_header() -> str | None:
    """``AK5_CLIENT_IP_HEADER`` if set (lowercased), else None."""
    raw = (os.environ.get("AK5_CLIENT_IP_HEADER") or "").strip().lower()
    return raw or None


def hosted_client_ip_config_ok() -> bool:
    """True when hosted profile has proxies and/or a platform client-IP header."""
    has_proxies = bool((os.environ.get("AK5_TRUSTED_PROXIES") or "").strip())
    return has_proxies or configured_client_ip_header() is not None


def validate_client_ip_header_name() -> None:
    """Reject unknown ``AK5_CLIENT_IP_HEADER`` on every profile (#125 review)."""
    header = configured_client_ip_header()
    if header is None:
        return
    if header not in ALLOWED_CLIENT_IP_HEADERS:
        allowed = ", ".join(sorted(ALLOWED_CLIENT_IP_HEADERS))
        raise ValueError(
            f"Invalid AK5_CLIENT_IP_HEADER={header!r}. Allowed values: {allowed}."
        )


def validate_hosted_client_ip_config() -> None:
    """Fail closed for hosted without a client-IP trust strategy (#125).

    Entry-point agnostic (lifespan): covers ``api/index.py`` / raw uvicorn, not
    only ``ak5 serve``'s ``assert_safe_bind``. Always validates header names
    even on ``local`` so a typo cannot silently weaken IP limits.
    """
    from ak5.server.config import hosted_profile

    validate_client_ip_header_name()
    if not hosted_profile():
        return
    if not hosted_client_ip_config_ok():
        raise ValueError(
            "AK5_DEPLOY_PROFILE=hosted requires AK5_TRUSTED_PROXIES (CIDR list) "
            "or AK5_CLIENT_IP_HEADER (platform-guaranteed header, e.g. "
            "x-real-ip / x-vercel-forwarded-for). See operations.md §2.1 / #125."
        )


def trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """``AK5_TRUSTED_PROXIES``: comma-separated IPs / CIDRs of reverse proxies we sit behind."""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw in (os.environ.get("AK5_TRUSTED_PROXIES") or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid AK5_TRUSTED_PROXIES entry %r", raw)
    return networks


def _ip_or_none(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(value: str, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    ip = _ip_or_none(value)
    return ip is not None and any(ip in net for net in networks)


def resolve_client_ip(headers: dict[str, str], fallback: str) -> str:
    """Resolve client IP from a lowercased header map + socket peer (#122, #125).

    ``AK5_CLIENT_IP_HEADER`` mode (platform edge):
    - If ``AK5_TRUSTED_PROXIES`` is also set, the socket peer must be in that
      list before the platform header is believed (same spoofing guard as XFF).
    - If proxies are empty, the header is trusted only under the deployment
      assumption that *only* the platform edge can reach this process
      (operations.md §2.1 — never expose that socket to the public internet).
    """
    header_mode = configured_client_ip_header()
    if header_mode:
        networks = trusted_proxy_networks()
        if networks and not _is_trusted(fallback, networks):
            return fallback
        raw = headers.get(header_mode)
        if raw:
            first = raw.split(",")[0].strip()
            if _ip_or_none(first) is not None:
                return first
        return fallback
    return client_ip_from_headers(
        headers.get("x-forwarded-for"),
        headers.get("x-real-ip"),
        fallback,
    )


def client_ip_from_headers(x_forwarded_for: str | None, x_real_ip: str | None, fallback: str) -> str:
    """The caller's IP via ``AK5_TRUSTED_PROXIES`` + ``X-Forwarded-For`` (#122).

    ``fallback`` is the socket peer. Forwarding headers are believed **only when the peer is a
    configured trusted proxy**; otherwise anyone could pick their own rate-limit bucket.
    With a trusted peer, ``X-Forwarded-For`` is walked from the right and the first hop that
    is not itself a trusted proxy is the client.

    Platform header mode (``AK5_CLIENT_IP_HEADER``) is handled by :func:`resolve_client_ip`.
    """
    networks = trusted_proxy_networks()
    if not networks or not _is_trusted(fallback, networks):
        return fallback
    hops = [hop.strip() for hop in (x_forwarded_for or "").split(",") if hop.strip()]
    for hop in reversed(hops):
        if _ip_or_none(hop) is None:
            return fallback  # garbage in the chain: do not guess
        if not _is_trusted(hop, networks):
            return hop
    if hops:
        return hops[0]  # every hop is a trusted proxy: the leftmost is as far as we can see
    if x_real_ip and _ip_or_none(x_real_ip) is not None:
        return x_real_ip.strip()
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
