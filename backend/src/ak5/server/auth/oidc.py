"""OIDC Authorization Code + PKCE helpers (identity.md §3.2)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

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
    if provider.oauth_userinfo and "github.com" in provider.issuer.lower():
        return {
            "authorization_endpoint": provider.authorization_endpoint
            or "https://github.com/login/oauth/authorize",
            "token_endpoint": provider.token_endpoint
            or "https://github.com/login/oauth/access_token",
            "jwks_uri": "",
            "userinfo_endpoint": provider.userinfo_endpoint or "https://api.github.com/user",
        }
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
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    # OIDC providers get nonce; pure OAuth userinfo adapters (GitHub) omit it.
    if not provider.oauth_userinfo:
        params["nonce"] = nonce
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
    headers = {"Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(endpoints["token_endpoint"], data=data, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("Token endpoint returned non-object JSON")
        return payload


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


async def fetch_oauth_userinfo(
    provider: OidcProvider,
    *,
    endpoints: dict[str, str],
    access_token: str,
) -> OidcClaims:
    """OAuth 2.0 userinfo adapter (identity.md §3.1 — e.g. GitHub)."""
    userinfo_url = endpoints.get("userinfo_endpoint") or ""
    if not userinfo_url:
        raise ValueError("userinfo_endpoint required for oauth_userinfo providers")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(userinfo_url, headers=headers)
        resp.raise_for_status()
        profile = resp.json()
        if not isinstance(profile, dict):
            raise ValueError("Invalid userinfo response")

        email: str | None = None
        email_verified = False
        if isinstance(profile.get("email"), str) and profile["email"]:
            email = profile["email"]
            email_verified = bool(profile.get("email_verified", False))

        # GitHub: primary email often omitted from /user — fetch /user/emails.
        if (
            email is None
            and "github.com" in provider.issuer.lower()
            and "api.github.com" in userinfo_url
        ):
            emails_resp = await client.get(
                "https://api.github.com/user/emails",
                headers=headers,
            )
            if emails_resp.status_code == 200:
                entries = emails_resp.json()
                if isinstance(entries, list):
                    primary = next(
                        (
                            e
                            for e in entries
                            if isinstance(e, dict) and e.get("primary") and e.get("email")
                        ),
                        None,
                    )
                    chosen = primary or next(
                        (e for e in entries if isinstance(e, dict) and e.get("email")),
                        None,
                    )
                    if isinstance(chosen, dict):
                        email = str(chosen["email"])
                        email_verified = bool(chosen.get("verified", False))

    subject = str(profile.get("id") or profile.get("sub") or "")
    if not subject:
        raise ValueError("userinfo missing subject id")
    name = str(
        profile.get("name")
        or profile.get("login")
        or profile.get("preferred_username")
        or email
        or subject
    )
    return OidcClaims(
        issuer=provider.issuer,
        subject=subject,
        email=email,
        email_verified=email_verified,
        display_name=name,
    )
