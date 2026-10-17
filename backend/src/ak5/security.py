"""Client-safe identify helpers; server JWT/web-auth helpers via lazy re-export.

``identify_headers_from_env`` / ``get_identify_secret`` must work in a base
(``ak5`` without ``[server]``) install. Heavier symbols resolve from
``ak5.server.security`` on first attribute access.
"""

from __future__ import annotations

import os
from typing import Any

IDENTIFY_SECRET_HEADER = "X-AK5-Identify-Secret"

__all__ = [
    "IDENTIFY_SECRET_HEADER",
    "get_identify_secret",
    "identify_headers_from_env",
]


def get_identify_secret() -> str:
    """Optional shared secret for ``POST /auth/identify`` when set."""
    for key in ("AK5_IDENTIFY_SECRET", "IDENTIFY_SECRET"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    try:
        from ak5.server.config import settings
    except ImportError:
        return ""
    return (settings.IDENTIFY_SECRET or "").strip()


def identify_headers_from_env() -> dict[str, str]:
    """Headers CLI/MCP/SDK should attach when calling identify (empty if unset)."""
    secret = get_identify_secret()
    if not secret:
        return {}
    return {IDENTIFY_SECRET_HEADER: secret}


def __getattr__(name: str) -> Any:
    """Lazy re-export of server-only security helpers (compat shim)."""
    from ak5.server import security as server_security

    try:
        return getattr(server_security, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
