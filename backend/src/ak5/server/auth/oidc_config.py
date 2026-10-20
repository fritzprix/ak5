"""OIDC / OAuth provider configuration (identity.md §3.1, §8)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OidcProvider:
    """One upstream IdP entry from ``AK5_OIDC_PROVIDERS``."""

    id: str
    issuer: str
    client_id: str
    client_secret: str = ""
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    # When True, use OAuth userinfo instead of ID token (e.g. GitHub).
    oauth_userinfo: bool = False
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    jwks_uri: str | None = None
    userinfo_endpoint: str | None = None
    # Test / mock: shared HS256 secret for ID tokens (no JWKS fetch).
    id_token_hs_secret: str | None = None


def _parse_providers_json(raw: str) -> list[OidcProvider]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("AK5_OIDC_PROVIDERS must be a JSON array")
    out: list[OidcProvider] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Each OIDC provider must be an object")
        pid = str(item.get("id") or item.get("name") or "").strip()
        issuer = str(item.get("issuer") or "").strip().rstrip("/")
        client_id = str(item.get("client_id") or "").strip()
        if not pid or not issuer or not client_id:
            raise ValueError("OIDC provider requires id, issuer, client_id")
        scopes_raw = item.get("scopes") or ["openid", "profile", "email"]
        if isinstance(scopes_raw, str):
            scopes = tuple(s.strip() for s in scopes_raw.split() if s.strip())
        else:
            scopes = tuple(str(s) for s in scopes_raw)
        is_github = "github.com" in issuer.lower() or bool(
            item.get("oauth_userinfo") or item.get("github")
        )
        default_scopes = (
            ("read:user", "user:email")
            if is_github
            else ("openid", "profile", "email")
        )
        out.append(
            OidcProvider(
                id=pid,
                issuer=issuer,
                client_id=client_id,
                client_secret=str(item.get("client_secret") or ""),
                scopes=scopes or default_scopes,
                oauth_userinfo=is_github,
                authorization_endpoint=item.get("authorization_endpoint"),
                token_endpoint=item.get("token_endpoint"),
                jwks_uri=item.get("jwks_uri"),
                userinfo_endpoint=item.get("userinfo_endpoint"),
                id_token_hs_secret=item.get("id_token_hs_secret"),
            )
        )
    return out


def load_oidc_providers() -> list[OidcProvider]:
    """Parse ``AK5_OIDC_PROVIDERS`` env (JSON array). Empty → no OIDC."""
    raw = (os.environ.get("AK5_OIDC_PROVIDERS") or "").strip()
    if not raw:
        return []
    return _parse_providers_json(raw)


def oidc_providers_configured() -> bool:
    """True when at least one OIDC provider is configured."""
    try:
        return bool(load_oidc_providers())
    except (ValueError, json.JSONDecodeError):
        return False


def get_oidc_provider(provider_id: str) -> OidcProvider | None:
    for provider in load_oidc_providers():
        if provider.id == provider_id:
            return provider
    return None


def public_base_url(*, bind_host: str = "127.0.0.1", bind_port: int = 8000) -> str:
    """Callback base URL from ``AK5_PUBLIC_URL`` or bind address (identity.md §8.1)."""
    explicit = (os.environ.get("AK5_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = bind_host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    scheme = "http" if host in ("127.0.0.1", "localhost", "::1") else "https"
    return f"{scheme}://{host}:{bind_port}"
