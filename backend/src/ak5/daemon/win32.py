"""Windows named-pipe transport for the client daemon (#182).

asyncio has no Unix domain sockets on Windows, so the daemon serves the same
HTTP app over a named pipe instead. Each pipe instance carries a protected DACL
that grants access only to the current user's SID and rejects remote clients.
Clients only talk to a pipe whose server process is the daemon pid recorded in
the user-only pid file, so a pipe squatted under the same name is refused.

Everything here is Windows-only; callers check ``sys.platform == "win32"``.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from ctypes import wintypes
from pathlib import Path

import httpcore
import httpx

PIPE_PREFIX = "\\\\.\\pipe\\"
PIPE_BUFFER_BYTES = 64 * 1024

_PIPE_ACCESS_DUPLEX = 0x00000003
_FILE_FLAG_OVERLAPPED = 0x40000000
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_READMODE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_PIPE_UNLIMITED_INSTANCES = 255
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1
_SDDL_REVISION_1 = 1
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5
_ERROR_BROKEN_PIPE = 109
_ERROR_SEM_TIMEOUT = 121
_ERROR_PIPE_BUSY = 231


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class _Win32:
    """Typed kernel32/advapi32 entry points, loaded on first use."""

    def __init__(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)

        def fn(dll: ctypes.WinDLL, name: str, restype: type, *argtypes: type) -> Callable[..., int]:
            f = getattr(dll, name)
            f.restype = restype
            f.argtypes = list(argtypes)
            return f

        h, d, b, lp = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPVOID
        pd = ctypes.POINTER(wintypes.DWORD)
        self.CreateNamedPipeW = fn(k32, "CreateNamedPipeW", h, wintypes.LPCWSTR, d, d, d, d, d, d, lp)
        self.CreateFileW = fn(k32, "CreateFileW", h, wintypes.LPCWSTR, d, d, lp, d, d, h)
        self.WaitNamedPipeW = fn(k32, "WaitNamedPipeW", b, wintypes.LPCWSTR, d)
        self.PeekNamedPipe = fn(k32, "PeekNamedPipe", b, h, lp, d, pd, pd, pd)
        self.ReadFile = fn(k32, "ReadFile", b, h, lp, d, pd, lp)
        self.WriteFile = fn(k32, "WriteFile", b, h, lp, d, pd, lp)
        self.CloseHandle = fn(k32, "CloseHandle", b, h)
        self.GetNamedPipeServerProcessId = fn(k32, "GetNamedPipeServerProcessId", b, h, ctypes.POINTER(wintypes.ULONG))
        self.GetCurrentProcess = fn(k32, "GetCurrentProcess", h)
        self.OpenProcess = fn(k32, "OpenProcess", h, d, b, d)
        self.GetExitCodeProcess = fn(k32, "GetExitCodeProcess", b, h, pd)
        self.LocalFree = fn(k32, "LocalFree", lp, lp)
        self.OpenProcessToken = fn(adv, "OpenProcessToken", b, h, d, ctypes.POINTER(h))
        self.GetTokenInformation = fn(adv, "GetTokenInformation", b, h, ctypes.c_int, lp, d, pd)
        self.ConvertSidToStringSidW = fn(adv, "ConvertSidToStringSidW", b, lp, ctypes.POINTER(wintypes.LPWSTR))
        self.ConvertStringSecurityDescriptorToSecurityDescriptorW = fn(
            adv,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            b,
            wintypes.LPCWSTR,
            d,
            ctypes.POINTER(lp),
            pd,
        )


_api: _Win32 | None = None


def _win() -> _Win32:
    global _api
    if _api is None:
        _api = _Win32()
    return _api


def _last_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def current_user_sid() -> str:
    """String SID of the user running this process (e.g. ``S-1-5-21-…``)."""
    api = _win()
    token = wintypes.HANDLE()
    if not api.OpenProcessToken(api.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _last_error()
    try:
        needed = wintypes.DWORD(0)
        api.GetTokenInformation(token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(needed))
        buf = ctypes.create_string_buffer(needed.value)
        if not api.GetTokenInformation(token, _TOKEN_USER_CLASS, buf, needed, ctypes.byref(needed)):
            raise _last_error()
        user = _SidAndAttributes.from_buffer(buf)
        text = wintypes.LPWSTR()
        if not api.ConvertSidToStringSidW(user.Sid, ctypes.byref(text)):
            raise _last_error()
        try:
            return str(text.value)
        finally:
            api.LocalFree(text)
    finally:
        api.CloseHandle(token)


def pipe_name_for(home: Path) -> str:
    """Per-user, per-``AK5_HOME`` pipe name, so separate homes never share a daemon."""
    key = f"{current_user_sid()}|{str(home).lower()}".encode()
    return f"{PIPE_PREFIX}ak5-daemon-{hashlib.sha256(key).hexdigest()[:24]}"


def pipe_exists(name: str) -> bool:
    """True when the pipe has a server, without consuming an instance."""
    if _win().WaitNamedPipeW(name, 1):
        return True
    return ctypes.get_last_error() == _ERROR_SEM_TIMEOUT  # exists, every instance busy


def process_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` sends CTRL_C_EVENT on Windows, so query the process instead."""
    api = _win()
    handle = api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD(0)
        if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        api.CloseHandle(handle)


# --------------------------------------------------------------------------- server


class _UserOnlySecurity:
    """SECURITY_ATTRIBUTES whose protected DACL allows only the current user."""

    def __init__(self) -> None:
        self._descriptor = wintypes.LPVOID()
        sddl = f"D:P(A;;GA;;;{current_user_sid()})"
        if not _win().ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(self._descriptor), None
        ):
            raise _last_error()
        self.attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), self._descriptor, False)

    def free(self) -> None:
        if self._descriptor:
            _win().LocalFree(self._descriptor)
            self._descriptor = wintypes.LPVOID()


def create_pipe_instance(name: str, security: _UserOnlySecurity, *, first: bool) -> int:
    flags = _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED
    if first:
        # Fails if any process already owns this name (squatting), instead of joining it.
        flags |= _FILE_FLAG_FIRST_PIPE_INSTANCE
    handle = _win().CreateNamedPipeW(
        name,
        flags,
        _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
        _PIPE_UNLIMITED_INSTANCES,
        PIPE_BUFFER_BYTES,
        PIPE_BUFFER_BYTES,
        0,
        ctypes.addressof(security.attributes),
    )
    if handle == _INVALID_HANDLE_VALUE or handle is None:
        raise _last_error()
    return int(handle)


if sys.platform == "win32":
    from asyncio import windows_events, windows_utils

    class UserOnlyPipeServer(windows_events.PipeServer):
        """``asyncio`` pipe server whose instances use :func:`create_pipe_instance`."""

        def __init__(self, address: str) -> None:
            self._security = _UserOnlySecurity()
            super().__init__(address)

        def _server_pipe_handle(self, first: bool) -> windows_utils.PipeHandle | None:
            if self.closed():
                return None
            pipe = windows_utils.PipeHandle(create_pipe_instance(self._address, self._security, first=first))
            self._free_instances.add(pipe)
            return pipe

        def close(self) -> None:
            super().close()
            self._security.free()

        __del__ = close

    def serve_pipe(
        loop: asyncio.AbstractEventLoop,
        protocol_factory: Callable[[], asyncio.Protocol],
        address: str,
    ) -> UserOnlyPipeServer:
        """``ProactorEventLoop.start_serving_pipe`` with :class:`UserOnlyPipeServer` instances.

        The accept loop mirrors CPython's; the stock method hard-codes a pipe server
        without a security descriptor or ``PIPE_REJECT_REMOTE_CLIENTS``.
        """
        if not isinstance(loop, windows_events.ProactorEventLoop):
            raise RuntimeError("the ak5 daemon needs the Proactor event loop on Windows")
        proactor = loop._proactor  # noqa: SLF001 — no public hook for secured pipe instances
        server = UserOnlyPipeServer(address)

        def accept(fut: asyncio.Future[windows_utils.PipeHandle] | None = None) -> None:
            pipe: windows_utils.PipeHandle | None = None
            try:
                if fut is not None:
                    pipe = fut.result()
                    server._free_instances.discard(pipe)  # noqa: SLF001
                    if server.closed():
                        pipe.close()
                        return
                    loop._make_duplex_pipe_transport(pipe, protocol_factory(), extra={"addr": address})  # noqa: SLF001
                pipe = server._get_unconnected_pipe()  # noqa: SLF001
                if pipe is None:
                    return
                next_fut = proactor.accept_pipe(pipe)
            except OSError:
                if pipe is not None and pipe.fileno() != -1:
                    pipe.close()
                if not server.closed():
                    loop.call_soon(accept)
            except asyncio.CancelledError:
                if pipe is not None:
                    pipe.close()
            else:
                server._accept_pipe_future = next_fut  # noqa: SLF001
                next_fut.add_done_callback(accept)

        loop.call_soon(accept)
        return server


# --------------------------------------------------------------------------- client


class PipeStream(httpcore.NetworkStream):
    """Blocking byte stream over a client pipe handle, with poll-based read timeouts."""

    def __init__(self, handle: int) -> None:
        self._handle = handle

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        api = _win()
        deadline = None if timeout is None else time.monotonic() + timeout
        available = wintypes.DWORD(0)
        delay = 0.001
        while True:
            if not api.PeekNamedPipe(self._handle, None, 0, None, ctypes.byref(available), None):
                if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                    return b""
                raise httpcore.ReadError(str(_last_error()))
            if available.value:
                break
            if deadline is not None and time.monotonic() >= deadline:
                raise httpcore.ReadTimeout("timed out reading from the ak5 daemon pipe")
            time.sleep(delay)
            delay = min(delay * 2, 0.02)
        size = min(max_bytes, available.value)
        buf = ctypes.create_string_buffer(size)
        read = wintypes.DWORD(0)
        if not api.ReadFile(self._handle, buf, size, ctypes.byref(read), None):
            if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                return b""
            raise httpcore.ReadError(str(_last_error()))
        return buf.raw[: read.value]

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        api = _win()
        view = memoryview(buffer)
        while view:
            chunk = bytes(view[:PIPE_BUFFER_BYTES])
            written = wintypes.DWORD(0)
            if not api.WriteFile(self._handle, chunk, len(chunk), ctypes.byref(written), None):
                raise httpcore.WriteError(str(_last_error()))
            view = view[written.value :]

    def close(self) -> None:
        if self._handle:
            _win().CloseHandle(self._handle)
            self._handle = 0

    def start_tls(
        self,
        ssl_context: object,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        raise httpcore.UnsupportedProtocol("TLS is not used on the local daemon pipe")


def open_pipe(name: str, *, expected_server_pid: int | None, timeout: float | None) -> PipeStream:
    """Connect to ``name`` and require that the daemon pid owns the server end."""
    api = _win()
    deadline = time.monotonic() + (timeout if timeout is not None else 10.0)
    while True:
        handle = api.CreateFileW(name, _GENERIC_READ | _GENERIC_WRITE, 0, None, _OPEN_EXISTING, 0, None)
        if handle not in (None, _INVALID_HANDLE_VALUE):
            break
        err = ctypes.get_last_error()
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if err != _ERROR_PIPE_BUSY or remaining_ms <= 0:
            if err == _ERROR_PIPE_BUSY:
                raise httpcore.ConnectTimeout("the ak5 daemon pipe stayed busy")
            raise httpcore.ConnectError(f"cannot open the ak5 daemon pipe {name}: {ctypes.WinError(err)}")
        api.WaitNamedPipeW(name, min(remaining_ms, 1000))
    stream = PipeStream(int(handle))
    server_pid = wintypes.ULONG(0)
    if not api.GetNamedPipeServerProcessId(int(handle), ctypes.byref(server_pid)):
        stream.close()
        raise httpcore.ConnectError(f"cannot identify the ak5 daemon pipe server: {_last_error()}")
    if expected_server_pid is None or server_pid.value != expected_server_pid:
        stream.close()
        raise httpcore.ConnectError(
            f"{name} is served by pid {server_pid.value}, not the ak5 daemon ({expected_server_pid}); refusing to connect"
        )
    return stream


class PipeNetworkBackend(httpcore.NetworkBackend):
    """Every connection goes to one pipe, whatever host the URL names."""

    def __init__(self, name: str, expected_server_pid: int | None) -> None:
        self._name = name
        self._expected_server_pid = expected_server_pid

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> httpcore.NetworkStream:
        return open_pipe(self._name, expected_server_pid=self._expected_server_pid, timeout=timeout)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> httpcore.NetworkStream:
        raise httpcore.UnsupportedProtocol("the ak5 daemon uses a named pipe on Windows")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


_EXCEPTIONS: tuple[tuple[type[Exception], type[httpx.TransportError]], ...] = (
    (httpcore.ConnectTimeout, httpx.ConnectTimeout),
    (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.WriteTimeout, httpx.WriteTimeout),
    (httpcore.PoolTimeout, httpx.PoolTimeout),
    (httpcore.ConnectError, httpx.ConnectError),
    (httpcore.ReadError, httpx.ReadError),
    (httpcore.WriteError, httpx.WriteError),
    (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore.LocalProtocolError, httpx.LocalProtocolError),
    (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
)


def _to_httpx(exc: Exception, request: httpx.Request) -> httpx.TransportError | None:
    for core_type, httpx_type in _EXCEPTIONS:
        if isinstance(exc, core_type):
            return httpx_type(str(exc), request=request)
    return None


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream: Iterable[bytes], request: httpx.Request) -> None:
        self._stream = stream
        self._request = request

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._stream
        except Exception as exc:
            mapped = _to_httpx(exc, self._request)
            if mapped is None:
                raise
            raise mapped from exc

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if callable(close):
            close()


class PipeTransport(httpx.BaseTransport):
    """``httpx`` transport that sends every request over the daemon's named pipe."""

    def __init__(self, name: str, *, expected_server_pid: int | None) -> None:
        self._pool = httpcore.ConnectionPool(network_backend=PipeNetworkBackend(name, expected_server_pid))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = self._pool.handle_request(core_request)
        except Exception as exc:
            mapped = _to_httpx(exc, request)
            if mapped is None:
                raise
            raise mapped from exc
        stream = response.stream
        if not isinstance(stream, Iterable):
            raise TypeError("sync connection pool returned an async stream")
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(stream, request),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._pool.close()
