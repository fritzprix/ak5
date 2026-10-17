"""Access-token resolution chokepoint (client.md).

Callers must use :func:`resolve_access_token` only. Issue #13 will replace the
body with daemon token-broker IPC; keep call sites unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Callable

_resolver: Callable[[], str | None] | None = None


def set_access_token_resolver(resolver: Callable[[], str | None] | None) -> None:
    """Register the CLI/session token resolver (or clear with ``None``)."""
    global _resolver
    _resolver = resolver


def resolve_access_token() -> str | None:
    """Return a Bearer access token, or ``None`` if unavailable."""
    if _resolver is not None:
        return _resolver()
    token = os.environ.get("AK5_ACTOR_TOKEN")
    return token if token else None
