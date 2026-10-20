"""Guard for CLI commands that require the ``ak5[server]`` extra."""

from __future__ import annotations

import sys

SERVER_INSTALL_HINT = (
    "AK5 server dependencies are not installed.\n"
    "Install the server extra and retry:\n"
    "  pip install 'ak5[server]'\n"
    "  # or: uv pip install 'ak5[server]'\n"
    "  # or (editable workspace): uv sync --extra server --all-packages"
)

# Modules that are declared only under the ``[server]`` optional extra.
_SERVER_MODULES = (
    "sqlalchemy",
    "aiosqlite",
    "greenlet",
    "sse_starlette",
)


def server_extra_available() -> bool:
    """Return True when core ``[server]`` dependencies can be imported."""
    for name in _SERVER_MODULES:
        try:
            __import__(name)
        except ImportError:
            return False
    return True


def require_server_extra() -> None:
    """Exit with a clear install hint when the server extra is missing."""
    missing: list[str] = []
    for name in _SERVER_MODULES:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if not missing:
        return
    print(SERVER_INSTALL_HINT, file=sys.stderr)
    print(f"(missing import(s): {', '.join(missing)})", file=sys.stderr)
    raise SystemExit(1)
