"""Build an :class:`AK5Client` from CLI session / env."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
from typing import Any

import httpx

from ak5.cli.config import get_api_url, get_token, require_auth_headers
from ak5.client.auth import resolve_access_token, set_access_token_resolver
from ak5.client.errors import EXIT_AUTH, ClientError
from ak5.client.http import AK5Client

# Wire CLI session/env token resolution through the client chokepoint (#13).
set_access_token_resolver(get_token)

_TEST_TRANSPORT: httpx.BaseTransport | None = None


class SyncASGITransport(httpx.BaseTransport):
    """Bridge async ``ASGITransport`` for sync ``httpx.Client`` (CLI / tests).

    Buffers the full response body so the sync client receives a ``SyncByteStream``.
    """

    def __init__(self, app: Any, *, raise_app_exceptions: bool = True) -> None:
        self._async = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _call() -> httpx.Response:
            response = await self._async.handle_async_request(request)
            chunks: list[bytes] = []
            async for chunk in response.aiter_raw():
                chunks.append(chunk)
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                request=request,
                extensions=response.extensions,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_call())

        # Already inside an event loop (pytest-asyncio): run in a worker thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _call()).result()


def set_test_transport(transport: httpx.BaseTransport | None) -> None:
    """Install an ASGI/mock transport for in-process CLI tests."""
    global _TEST_TRANSPORT
    _TEST_TRANSPORT = transport


def set_test_app(app: Any) -> None:
    """Convenience: wire CLI client to an ASGI app via :class:`SyncASGITransport`."""
    set_test_transport(SyncASGITransport(app))


def _optional_transport() -> httpx.BaseTransport | None:
    return _TEST_TRANSPORT


def open_client(
    *,
    require_auth: bool = True,
    api_url: str | None = None,
    skip_version_check: bool | None = None,
    transport: httpx.BaseTransport | None = None,
) -> AK5Client:
    """Create a client bound to the current session.

    Raises :class:`ClientError` / ``SystemExit`` when auth is required but missing.
    """
    url = api_url or get_api_url()
    headers: dict[str, str] = {}
    if require_auth:
        try:
            # Env → daemon profile → legacy session (may SystemExit 8/9).
            auth_headers = require_auth_headers(url)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else EXIT_AUTH
            # Map legacy auth SystemExit(1) → exit 8 for API contract.
            if code == 1:
                code = EXIT_AUTH
            raise ClientError(
                "Authentication required",
                exit_code=code,
            ) from exc
        token = None
        auth = auth_headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            try:
                token = resolve_access_token()
            except ClientError:
                raise
        if not token:
            raise ClientError("Authentication required", exit_code=EXIT_AUTH)
        headers = {"Authorization": f"Bearer {token}"}

    skip = skip_version_check
    if skip is None:
        skip = os.environ.get("AK5_SKIP_VERSION_CHECK", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    return AK5Client(
        url,
        headers=headers,
        transport=transport if transport is not None else _optional_transport(),
        skip_version_check=skip,
    )


def json_flag_from_ctx(ctx: Any) -> bool | None:
    """Read global ``--json`` from click context, if present."""
    if ctx is None:
        return None
    obj = getattr(ctx, "obj", None)
    if isinstance(obj, dict) and "json" in obj:
        return bool(obj["json"])
    parent = getattr(ctx, "parent", None)
    if parent is not None:
        return json_flag_from_ctx(parent)
    return None
