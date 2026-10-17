"""Peer credential checks for UDS (client.md §5)."""

from __future__ import annotations

import os
import struct
import sys
from typing import Any


def peer_uid_from_socket(sock: Any) -> int | None:
    """Return peer uid via ``SO_PEERCRED`` (Linux) / ``getpeereid`` (macOS)."""
    if sock is None:
        return None
    try:
        if sys.platform == "linux":
            # struct ucred { pid_t; uid_t; gid_t; }
            SO_PEERCRED = 17
            raw = sock.getsockopt(socket_sol_socket(), SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", raw)
            return int(uid)
        if sys.platform == "darwin":
            import socket as _socket

            getpeereid = getattr(_socket, "getpeereid", None)
            if getpeereid is None:
                return None
            uid, _gid = getpeereid(sock.fileno())
            return int(uid)
    except OSError:
        return None
    return None


def socket_sol_socket() -> int:
    import socket

    return socket.SOL_SOCKET


def require_same_uid(sock: Any) -> None:
    """Raise PermissionError if peer uid != our uid (when peercred available)."""
    peer = peer_uid_from_socket(sock)
    if peer is None:
        return
    if peer != os.getuid():
        raise PermissionError(f"peer uid {peer} != {os.getuid()}")
