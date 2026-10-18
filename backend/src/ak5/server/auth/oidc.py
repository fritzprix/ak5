"""OIDC Authorization Code + PKCE helpers (identity.md §3.2)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

# secrets used for PKCE + nonce compare_digest
import httpx
import jwt

from ak5.server.auth.oidc_config import OidcProvider


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_state_nonce() -> tuple[str, str]:
    return secrets.token_urlsafe(24), secrets.token_urlsafe(24)


@dataclass(frozen=True)
class OidcClaims:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    display_name: str


async def discover_endpoints(provider: OidcProvider) -> dict[str, str]:
    """Resolve authorization/token/jwks URLs (explicit config wins over discovery)."""
    if (
        provider.authorization_endpoint
        and provider.token_endpoint
        and (provider.jwks_uri or provider.id_token_hs_secret or provider.oauth_userinfo)
    ):
        return {
            "authorization_endpoint": provider.authorization_endpoint,
            "token_endpoint": provider.token_endpoint,
            "jwks_uri": provider.jwks_uri or "",
            "userinfo_endpoint": provider.userinfo_endpoint or "",
        }
    url = f"{provider.issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        doc = resp.json()
    return {
        "authorization_endpoint": provider.authorization_endpoint
        or str(doc["authorization_endpoint"]),
        "token_endpoint": provider.token_endpoint or str(doc["token_endpoint"]),
        "jwks_uri": provider.jwks_uri or str(doc.get("jwks_uri") or ""),
        "userinfo_endpoint": provider.userinfo_endpoint
        or str(doc.get("userinfo_endpoint") or ""),
    }


def build_authorize_url(
    provider: OidcProvider,
    *,
    endpoints: dict[str, str],
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{endpoints['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(
    provider: OidcProvider,
    *,
    endpoints: dict[str, str],
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
        "code_verifier": code_verifier,
    }
    if provider.client_secret:
        data["client_secret"] = provider.client_secret
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(endpoints["token_endpoint"], data=data)
        resp.raise_for_status()
        return resp.json()


def verify_id_token(
    provider: OidcProvider,
    id_token: str,
    *,
    nonce: str,
    jwks: dict[str, Any] | None = None,
) -> OidcClaims:
    """Verify ID token signature and claims (iss/aud/nonce/exp)."""
    options = {"require": ["exp", "iss", "aud", "sub"]}
    if provider.id_token_hs_secret:
        payload = jwt.decode(
            id_token,
            provider.id_token_hs_secret,
            algorithms=["HS256"],
            audience=provider.client_id,
            issuer=provider.issuer,
            options=options,
        )
    else:
        if not jwks:
            raise ValueError("JWKS required for asymmetric ID token verification")
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        key = None
        for jwk in jwks.get("keys", []):
            if kid is None or jwk.get("kid") == kid:
                key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
                break
        if key is None:
            raise ValueError("No matching JWK for ID token")
        payload = jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=provider.client_id,
            issuer=provider.issuer,
            options=options,
        )

    token_nonce = str(payload.get("nonce") or "")
    if not token_nonce or not secrets.compare_digest(token_nonce, nonce):
        raise ValueError("nonce mismatch")

    email = payload.get("email")
    email_verified = bool(payload.get("email_verified", False))
    name = str(payload.get("name") or payload.get("preferred_username") or email or payload["sub"])
    return OidcClaims(
        issuer=str(payload["iss"]),
        subject=str(payload["sub"]),
        email=str(email) if email else None,
        email_verified=email_verified,
        display_name=name,
    )


async def fetch_jwks(jwks_uri: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid JWKS document")
        return data
