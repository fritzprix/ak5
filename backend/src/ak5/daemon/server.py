"""Run / stop the client daemon on a Unix domain socket (named pipe on Windows)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import uvicorn
from uvicorn.server import STARTUP_FAILURE

from ak5.daemon.app import create_daemon_app
from ak5.daemon.paths import chmod_socket, daemon_endpoint, ensure_socket_parent, pid_path, socket_path
from ak5.daemon.state import DaemonState

_WINDOWS = sys.platform == "win32"

logger = logging.getLogger("ak5.daemon")


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
    if _WINDOWS:
        from ak5.daemon.win32 import process_alive

        return process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _endpoint_ready(sock: Path | None = None) -> bool:
    if _WINDOWS:
        from ak5.daemon.win32 import pipe_exists

        return pipe_exists(daemon_endpoint())
    return (sock or socket_path()).exists()


def is_daemon_running() -> bool:
    pid = read_pid()
    return bool(pid and pid_alive(pid) and _endpoint_ready())


def stop_daemon(*, timeout: float = 5.0) -> bool:
    pid = read_pid()
    if pid and pid_alive(pid):
        # On Windows SIGTERM maps to TerminateProcess, so there is no later SIGKILL step.
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not pid_alive(pid):
                break
            time.sleep(0.05)
        if pid_alive(pid) and not _WINDOWS:
            os.kill(pid, signal.SIGKILL)
    leftovers = [pid_path()] if _WINDOWS else [pid_path(), socket_path()]
    for path in leftovers:
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


class NamedPipeServer(uvicorn.Server):
    """uvicorn serving the daemon app on a user-only named pipe (Windows, #182)."""

    def __init__(self, config: uvicorn.Config, pipe_name: str) -> None:
        super().__init__(config)
        self._pipe_name = pipe_name
        self._close_pipe: Callable[[], None] | None = None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        from ak5.daemon.win32 import serve_pipe

        await self.lifespan.startup()
        if self.lifespan.should_exit:
            sys.exit(STARTUP_FAILURE)
        config = self.config

        def create_protocol() -> asyncio.Protocol:
            return config.http_protocol_class(
                config=config,
                server_state=self.server_state,
                app_state=self.lifespan.state,
            )

        try:
            pipe_server = serve_pipe(asyncio.get_running_loop(), create_protocol, self._pipe_name)
        except OSError as exc:
            logger.error("ak5 daemon: cannot create pipe %s: %s", self._pipe_name, exc)
            await self.lifespan.shutdown()
            sys.exit(STARTUP_FAILURE)
        self._close_pipe = pipe_server.close
        # Only once the pipe is ours: clients pin the pipe server to this pid.
        write_pid()
        self.servers = []
        self.started = True

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        if self._close_pipe is not None:
            self._close_pipe()
            self._close_pipe = None
        await super().shutdown(sockets)


def _log_level() -> str:
    return os.environ.get("AK5_DAEMON_LOG_LEVEL", "warning")


def _run_daemon_on_pipe() -> None:
    state = DaemonState()
    app = create_daemon_app(state)
    server = NamedPipeServer(uvicorn.Config(app, log_level=_log_level(), lifespan="on"), daemon_endpoint())
    try:
        asyncio.run(server.serve())
    finally:
        state.close()
        # A second daemon that lost the pipe must not remove the running daemon's pid file.
        if read_pid() == os.getpid():
            with contextlib.suppress(OSError):
                pid_path().unlink(missing_ok=True)


def run_daemon(*, sock: Path | None = None) -> None:
    """Blocking foreground run (``ak5 daemon run``)."""
    if _WINDOWS:
        _run_daemon_on_pipe()
        return

    from ak5.daemon.paths import validate_socket_path

    path = sock or socket_path()
    try:
        validate_socket_path(path)
    except ValueError as exc:
        raise SystemExit(f"ak5 daemon: {exc}") from exc
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
        log_level=_log_level(),
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


def detached_popen_kwargs() -> dict[str, int | bool]:
    """Keep the daemon out of the caller's console signals (Ctrl+C) on every platform."""
    if _WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def start_daemon_background(*, sock: Path | None = None) -> int:
    """Fork/spawn ``daemon run`` and return child pid."""
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
            **detached_popen_kwargs(),
        )
    deadline = time.time() + 5
    while time.time() < deadline:
        if _WINDOWS and is_daemon_running():
            break
        if not _WINDOWS and _endpoint_ready(sock):
            chmod_socket(sock or socket_path())
            break
        if proc.poll() is not None:
            raise RuntimeError(f"daemon exited early: {proc.returncode}")
        time.sleep(0.05)
    # A venv launcher (Windows) re-spawns the interpreter, so prefer the pid the daemon recorded.
    return read_pid() or proc.pid
