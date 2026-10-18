"""Access-token resolution chokepoint (client.md §3.3–§3.4 / #61).

Order: ``AK5_TOKEN`` / ``AK5_ACTOR_TOKEN`` → daemon ``/v1/token`` (autostart when
a profile credential exists) → session resolver.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from ak5.client.errors import ClientError

_resolver: Callable[[], str | None] | None = None


def set_access_token_resolver(resolver: Callable[[], str | None] | None) -> None:
    """Register the CLI/session token resolver (or clear with ``None``)."""
    global _resolver
    _resolver = resolver


def resolve_access_token() -> str | None:
    """Return a Bearer access token, or ``None`` if unavailable.

    Raises :class:`ClientError` with exit 9 when a profile credential exists but
    the daemon cannot be started (``AK5_DAEMON_AUTOSTART=0`` or start failure).
    """
    for key in ("AK5_TOKEN", "AK5_ACTOR_TOKEN"):
        env_token = os.environ.get(key, "").strip()
        if env_token:
            return env_token

    try:
        from ak5.daemon.credentials import load_credential
        from ak5.daemon.ipc import fetch_token
        from ak5.daemon.profiles import resolve_profile_name
        from ak5.daemon.server import is_daemon_running

        profile = resolve_profile_name(None)
        has_cred = bool(profile and load_credential(profile) is not None)
        if has_cred:
            # client.md §6 — autostart when a logged-in profile exists.
            body = fetch_token(profile, ensure=True)
            token = str(body.get("access_token") or "")
            if token:
                return token
        elif is_daemon_running():
            body = fetch_token(None, ensure=False)
            token = str(body.get("access_token") or "")
            if token:
                return token
    except ClientError:
        raise
    except Exception:  # noqa: BLE001 — fall through to session resolver
        pass

    if _resolver is not None:
        return _resolver()
    return None
