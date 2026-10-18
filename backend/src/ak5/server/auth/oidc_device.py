"""Upstream IdP device authorization (identity.md §8.1 — no redirect URI)."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ak5.server.auth.oidc import OidcClaims, fetch_oauth_userinfo
from ak5.server.auth.oidc_config import OidcProvider

# device_code → pending upstream poll state (in-process; TTL from IdP + size capped).
_PENDING_MAX = 256
_pending: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class UpstreamDeviceStart:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


def _purge_pending_locked(now: float) -> None:
    expired = [
        code for code, meta in _pending.items() if float(meta["expires_at"]) <= now
    ]
    for code in expired:
        del _pending[code]


def _evict_oldest_pending_locked() -> None:
    if not _pending:
        return
    oldest = min(_pending.items(), key=lambda item: float(item[1]["expires_at"]))[0]
    del _pending[oldest]


def clear_upstream_device_pending_for_tests() -> None:
    with _lock:
        _pending.clear()


def upstream_device_pending_count_for_tests() -> int:
    with _lock:
        return len(_pending)


def _github_device_endpoints() -> dict[str, str]:
    return {
        "device_authorization_endpoint": "https://github.com/login/device/code",
        "token_endpoint": "https://github.com/login/oauth/access_token",
        "userinfo_endpoint": "https://api.github.com/user",
    }


def _google_device_endpoints() -> dict[str, str]:
    # Google OAuth 2.0 for TV and Limited Input devices.
    return {
        "device_authorization_endpoint": "https://oauth2.googleapis.com/device/code",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    }


def upstream_device_endpoints(provider: OidcProvider) -> dict[str, str] | None:
    """Return device-flow endpoints when the provider supports them."""
    issuer = provider.issuer.lower()
    if "github.com" in issuer or provider.oauth_userinfo:
        return _github_device_endpoints()
    if "accounts.google.com" in issuer or "googleapis.com" in issuer:
        return _google_device_endpoints()
    return None


async def start_upstream_device_flow(provider: OidcProvider) -> UpstreamDeviceStart:
    """POST to the IdP device authorization endpoint."""
    endpoints = upstream_device_endpoints(provider)
    if endpoints is None:
        raise ValueError(f"Provider '{provider.id}' does not support upstream device flow")

    data = {
        "client_id": provider.client_id,
        "scope": " ".join(provider.scopes),
    }
    if provider.client_secret and "github.com" not in provider.issuer.lower():
        data["client_secret"] = provider.client_secret

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            endpoints["device_authorization_endpoint"],
            data=data,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError("Invalid device authorization response")

    device_code = str(payload["device_code"])
    user_code = str(payload["user_code"])
    verification_uri = str(
        payload.get("verification_uri") or payload.get("verification_url") or ""
    )
    if not verification_uri:
        raise ValueError("IdP omitted verification_uri")
    expires_in = int(payload.get("expires_in") or 900)
    interval = int(payload.get("interval") or 5)
    complete = payload.get("verification_uri_complete")
    complete_s = str(complete) if complete else None

    now = time.time()
    with _lock:
        _purge_pending_locked(now)
        while len(_pending) >= _PENDING_MAX:
            _evict_oldest_pending_locked()
        _pending[device_code] = {
            "provider_id": provider.id,
            "interval": interval,
            "expires_at": now + expires_in,
            "last_poll": 0.0,
        }

    return UpstreamDeviceStart(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=complete_s,
        expires_in=expires_in,
        interval=interval,
    )


class UpstreamDevicePending(Exception):
    """authorization_pending / slow_down from the IdP."""

    def __init__(self, error: str, *, retry_after: int | None = None) -> None:
        self.error = error
        self.retry_after = retry_after
        super().__init__(error)


async def poll_upstream_device_flow(
    provider: OidcProvider,
    *,
    device_code: str,
) -> OidcClaims:
    """Poll the IdP token endpoint until access is granted; return identity claims."""
    endpoints = upstream_device_endpoints(provider)
    if endpoints is None:
        raise ValueError(f"Provider '{provider.id}' does not support upstream device flow")

    now = time.time()
    with _lock:
        _purge_pending_locked(now)
        meta = _pending.get(device_code)
    if meta is None:
        raise ValueError("Unknown or expired device_code")
    if now > float(meta["expires_at"]):
        with _lock:
            _pending.pop(device_code, None)
        raise ValueError("expired_token")

    interval = int(meta.get("interval") or 5)
    now = time.time()
    last = float(meta.get("last_poll") or 0.0)
    if last and now - last < interval:
        raise UpstreamDevicePending("slow_down", retry_after=interval)
    with _lock:
        if device_code in _pending:
            _pending[device_code]["last_poll"] = now

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": provider.client_id,
    }
    if provider.client_secret:
        data["client_secret"] = provider.client_secret

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            endpoints["token_endpoint"],
            data=data,
            headers={"Accept": "application/json"},
        )
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            payload = {}

    error = payload.get("error")
    if error in ("authorization_pending", "slow_down"):
        if error == "slow_down":
            with _lock:
                if device_code in _pending:
                    _pending[device_code]["interval"] = interval + 5
            raise UpstreamDevicePending("slow_down", retry_after=interval + 5)
        raise UpstreamDevicePending("authorization_pending", retry_after=interval)
    if error in ("expired_token", "access_denied"):
        with _lock:
            _pending.pop(device_code, None)
        raise ValueError(str(error))
    if resp.status_code >= 400 and error:
        raise ValueError(str(error))
    resp.raise_for_status()

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Token response missing access_token")

    # Prefer userinfo adapter (GitHub / Google userinfo).
    claims = await fetch_oauth_userinfo(
        provider,
        endpoints=endpoints,
        access_token=access_token,
    )
    with _lock:
        _pending.pop(device_code, None)
    return claims


def mint_local_device_code_for_tests(provider_id: str, *, interval: int = 1) -> str:
    """Test helper: register a synthetic pending device_code."""
    code = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        _purge_pending_locked(now)
        while len(_pending) >= _PENDING_MAX:
            _evict_oldest_pending_locked()
        _pending[code] = {
            "provider_id": provider_id,
            "interval": interval,
            "expires_at": now + 600,
            "last_poll": 0.0,
        }
    return code
