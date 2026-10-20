"""CLI → daemon HTTP client over UDS (client.md §5)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ak5.client.errors import EXIT_DAEMON, ClientError
from ak5.contracts.errors import ErrorCode
from ak5.daemon.paths import socket_path
from ak5.daemon.server import is_daemon_running, start_daemon_background


def _transport(sock: str | None = None) -> httpx.HTTPTransport:
    path = sock or str(socket_path())
    return httpx.HTTPTransport(uds=path)


def daemon_client(*, sock: str | None = None, timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(
        transport=_transport(sock),
        base_url="http://ak5-daemon",
        timeout=timeout,
    )


def ensure_daemon(*, autostart: bool | None = None) -> None:
    """Ensure daemon is running; autostart unless ``AK5_DAEMON_AUTOSTART=0``."""
    if is_daemon_running():
        return
    if autostart is None:
        flag = os.environ.get("AK5_DAEMON_AUTOSTART", "1").strip().lower()
        autostart = flag not in ("0", "false", "no", "off")
    if not autostart:
        raise ClientError(
            "ak5 daemon is not running",
            code=ErrorCode.DAEMON_UNAVAILABLE,
            exit_code=EXIT_DAEMON,
        )
    try:
        start_daemon_background()
    except Exception as exc:  # noqa: BLE001
        raise ClientError(
            f"failed to start ak5 daemon: {exc}",
            code=ErrorCode.DAEMON_UNAVAILABLE,
            exit_code=EXIT_DAEMON,
        ) from exc
    if not is_daemon_running():
        raise ClientError(
            "ak5 daemon did not become ready",
            code=ErrorCode.DAEMON_UNAVAILABLE,
            exit_code=EXIT_DAEMON,
        )


def fetch_token(profile: str | None = None, *, ensure: bool = True) -> dict[str, Any]:
    if ensure:
        ensure_daemon()
    with daemon_client() as client:
        resp = client.post("/v1/token", json={"profile": profile})
        if resp.status_code == 401:
            raise ClientError(
                resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else "unauthorized",
                code=ErrorCode.UNAUTHENTICATED,
                exit_code=8,
                http_status=401,
            )
        resp.raise_for_status()
        return resp.json()


def get_status(*, ensure: bool = False) -> dict[str, Any]:
    if ensure:
        ensure_daemon()
    elif not is_daemon_running():
        raise ClientError(
            "ak5 daemon is not running",
            code=ErrorCode.DAEMON_UNAVAILABLE,
            exit_code=EXIT_DAEMON,
        )
    with daemon_client() as client:
        resp = client.get("/v1/status")
        resp.raise_for_status()
        return resp.json()
