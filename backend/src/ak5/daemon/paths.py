"""Daemon filesystem layout (client.md §2 · §5)."""

from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path


def ak5_home() -> Path:
    """``~/.ak5`` (override: ``AK5_HOME``)."""
    override = os.environ.get("AK5_HOME", "").strip()
    path = Path(override).expanduser().resolve() if override else (Path.home() / ".ak5").resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)
    return path


def profiles_path() -> Path:
    return ak5_home() / "profiles.toml"


def credentials_dir() -> Path:
    d = ak5_home() / "credentials"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        d.chmod(0o700)
    return d


def daemon_state_dir() -> Path:
    d = ak5_home() / "daemon"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        d.chmod(0o700)
    return d


def state_db_path() -> Path:
    return daemon_state_dir() / "state.db"


def pid_path() -> Path:
    return daemon_state_dir() / "daemon.pid"


def runtime_dir() -> Path:
    """Socket directory: ``$XDG_RUNTIME_DIR/ak5`` or ``~/.ak5/run``."""
    xdg = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    d = Path(xdg).expanduser().resolve() / "ak5" if xdg else ak5_home() / "run"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        d.chmod(0o700)
    return d


def socket_path() -> Path:
    override = os.environ.get("AK5_DAEMON_SOCK", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return runtime_dir() / "daemon.sock"


def ensure_socket_parent(path: Path) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        parent.chmod(0o700)


def chmod_socket(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600


def dead_letter_path() -> Path:
    return daemon_state_dir() / "dead-letter.jsonl"


# Linux AF_UNIX sun_path is typically 108 bytes including NUL.
_AF_UNIX_MAX = 104


def validate_socket_path(path: Path | None = None) -> Path:
    """Raise ValueError if the UDS path is too long for AF_UNIX."""
    target = path or socket_path()
    encoded = str(target).encode("utf-8")
    if len(encoded) > _AF_UNIX_MAX:
        raise ValueError(
            f"daemon socket path too long for AF_UNIX ({len(encoded)} > {_AF_UNIX_MAX}): {target}. "
            "Set XDG_RUNTIME_DIR to a short path or AK5_DAEMON_SOCK=/tmp/ak5.sock"
        )
    return target
