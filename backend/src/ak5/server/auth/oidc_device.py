"""Upstream IdP device authorization (identity.md §8.1 — no redirect URI)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.server.auth.oidc import OidcClaims, fetch_oauth_userinfo
from ak5.server.auth.oidc_config import OidcProvider
from ak5.server.auth.pending_state import (
    claim_device_poll,
    delete_device_pending,
    load_device_pending,
    set_device_interval,
    store_device_pending,
)

# Pending upstream poll state lives in the database (``device_pending``, #107): shared by
# every instance, expired by TTL, capped per client key instead of evicting others.


@dataclass(frozen=True)
class UpstreamDeviceStart:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


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


async def start_upstream_device_flow(
    db: AsyncSession, provider: OidcProvider, *, client_key: str
) -> UpstreamDeviceStart:
    """POST to the IdP device authorization endpoint and remember the code for polling."""
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

    # PendingLimitError (this client already holds the maximum) propagates to the router.
    await store_device_pending(
        db,
        device_code=device_code,
        provider_id=provider.id,
        interval=interval,
        expires_in=expires_in,
        client_key=client_key,
    )

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
    db: AsyncSession,
    provider: OidcProvider,
    *,
    device_code: str,
) -> OidcClaims:
    """Poll the IdP token endpoint until access is granted; return identity claims."""
    endpoints = upstream_device_endpoints(provider)
    if endpoints is None:
        raise ValueError(f"Provider '{provider.id}' does not support upstream device flow")

    pending = await load_device_pending(db, device_code)
    if pending is None:
        raise ValueError("Unknown or expired device_code")

    interval = int(pending.interval or 5)
    # Atomic across instances: only one poller wins each interval slot.
    if not await claim_device_poll(db, device_code, interval=interval):
        raise UpstreamDevicePending("slow_down", retry_after=interval)

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
            await set_device_interval(db, device_code, interval + 5)
            raise UpstreamDevicePending("slow_down", retry_after=interval + 5)
        raise UpstreamDevicePending("authorization_pending", retry_after=interval)
    if error in ("expired_token", "access_denied"):
        await delete_device_pending(db, device_code)
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
    await delete_device_pending(db, device_code)
    return claims
