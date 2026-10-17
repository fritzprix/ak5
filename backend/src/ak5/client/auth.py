"""Access-token resolution chokepoint (client.md §3.3–§3.4).

Order: ``AK5_TOKEN`` / ``AK5_ACTOR_TOKEN`` → daemon ``/v1/token`` (if running)
→ session resolver. Autostart is handled by CLI commands that require the daemon,
not by every token lookup (keeps pytest / ``AK5_TOKEN`` CI paths quiet).
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
    for key in ("AK5_TOKEN", "AK5_ACTOR_TOKEN"):
        env_token = os.environ.get(key, "").strip()
        if env_token:
            return env_token

    try:
        from ak5.daemon.ipc import fetch_token
        from ak5.daemon.server import is_daemon_running

        if is_daemon_running():
            body = fetch_token(os.environ.get("AK5_PROFILE") or None, ensure=False)
            token = str(body.get("access_token") or "")
            if token:
                return token
    except Exception:  # noqa: BLE001 — fall through to session resolver
        pass

    if _resolver is not None:
        return _resolver()
    return None
