"""Run / stop the client daemon on a Unix domain socket."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from pathlib import Path

import uvicorn

from ak5.daemon.app import create_daemon_app
from ak5.daemon.paths import chmod_socket, ensure_socket_parent, pid_path, socket_path
from ak5.daemon.state import DaemonState


def write_pid(path: Path | None = None) -> None:
    p = path or pid_path()
    p.write_text(str(os.getpid()), encoding="utf-8")
    with contextlib.suppress(OSError):
        p.chmod(0o600)


def read_pid(path: Path | None = None) -> int | None:
    p = path or pid_path()
    if not p.is_file():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_daemon_running() -> bool:
    pid = read_pid()
    return bool(pid and pid_alive(pid) and socket_path().exists())


def stop_daemon(*, timeout: float = 5.0) -> bool:
    pid = read_pid()
    sock = socket_path()
    if pid and pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not pid_alive(pid):
                break
            time.sleep(0.05)
        if pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
    for path in (pid_path(), sock):
        if path.exists():
            with contextlib.suppress(OSError):
                path.unlink()
    return True


def _install_peercred_guard() -> None:
    """Close UDS connections whose peer uid != daemon uid (client.md §5)."""
    from ak5.daemon.peercred import require_same_uid

    def _wrap(cls: type) -> None:
        orig = cls.connection_made

        def connection_made(self: object, transport: object) -> None:
            get_extra = getattr(transport, "get_extra_info", None)
            sock = get_extra("socket") if callable(get_extra) else None
            try:
                require_same_uid(sock)
            except PermissionError:
                close = getattr(transport, "close", None)
                if callable(close):
                    close()
                return
            orig(self, transport)

        setattr(cls, "connection_made", connection_made)

    with contextlib.suppress(Exception):
        from uvicorn.protocols.http.h11_impl import H11Protocol

        _wrap(H11Protocol)
    with contextlib.suppress(Exception):
        from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

        _wrap(HttpToolsProtocol)


def run_daemon(*, sock: Path | None = None) -> None:
    """Blocking foreground run (``ak5 daemon run``)."""
    path = sock or socket_path()
    ensure_socket_parent(path)
    if path.exists():
        path.unlink()
    state = DaemonState()
    app = create_daemon_app(state)
    write_pid()
    _install_peercred_guard()

    config = uvicorn.Config(
        app,
        uds=str(path),
        log_level=os.environ.get("AK5_DAEMON_LOG_LEVEL", "warning"),
        lifespan="on",
    )
    server = uvicorn.Server(config)

    def _cleanup(*_args: object) -> None:
        server.should_exit = True

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    try:
        # chmod after bind: uvicorn creates the socket during serve()
        server.run()
    finally:
        state.close()
        for p in (pid_path(), path):
            if p.exists():
                with contextlib.suppress(OSError):
                    p.unlink()


def start_daemon_background(*, sock: Path | None = None) -> int:
    """Fork/spawn ``daemon run`` and return child pid."""
    import subprocess

    if is_daemon_running():
        pid = read_pid()
        return int(pid or 0)

    env = os.environ.copy()
    if sock is not None:
        env["AK5_DAEMON_SOCK"] = str(sock)

    log_path = pid_path().parent / "daemon.log"
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ak5.cli.main", "daemon", "run"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    # Wait briefly for socket
    target = sock or socket_path()
    deadline = time.time() + 5
    while time.time() < deadline:
        if target.exists():
            chmod_socket(target)
            break
        if proc.poll() is not None:
            raise RuntimeError(f"daemon exited early: {proc.returncode}")
        time.sleep(0.05)
    return proc.pid
