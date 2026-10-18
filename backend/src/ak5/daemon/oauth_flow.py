"""OAuth device grant + refresh helpers for the client daemon (client.md §3 / #26)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from ak5.client.errors import EXIT_AUTH, ClientError
from ak5.contracts.errors import ErrorCode

CLI_CLIENT_ID = "ak5-cli"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

# Optional test hook — SyncASGITransport against the gateway app.
_TEST_TRANSPORT: httpx.BaseTransport | None = None


def set_oauth_test_transport(transport: httpx.BaseTransport | None) -> None:
    """Wire daemon OAuth HTTP to an in-process ASGI app (tests)."""
    global _TEST_TRANSPORT
    _TEST_TRANSPORT = transport


def server_origin(server: str) -> str:
    """Strip trailing ``/api/v1`` so ``/oauth/*`` resolves on the gateway origin."""
    s = (server or "").strip().rstrip("/")
    if s.endswith("/api/v1"):
        s = s[: -len("/api/v1")]
    return s or "http://127.0.0.1:8000"


def _client(*, timeout: float = 30.0) -> httpx.Client:
    kwargs: dict[str, Any] = {"timeout": timeout}
    if _TEST_TRANSPORT is not None:
        kwargs["transport"] = _TEST_TRANSPORT
    return httpx.Client(**kwargs)


@dataclass(frozen=True)
class DeviceAuthStarted:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: str
    token_type: str = "Bearer"
    agent_id: str = ""


def start_device_login(
    server: str,
    *,
    principal: Literal["user", "agent"] = "agent",
    name: str | None = None,
    workflow_role: str | None = None,
    capabilities: list[str] | None = None,
    workspace_id: str | None = None,
) -> DeviceAuthStarted:
    origin = server_origin(server)
    body: dict[str, Any] = {
        "client_id": CLI_CLIENT_ID,
        "principal": principal,
    }
    if name:
        body["name"] = name
    if workflow_role:
        body["workflow_role"] = workflow_role
    if capabilities is not None:
        body["capabilities"] = capabilities
    if workspace_id:
        body["workspace_id"] = workspace_id

    with _client() as client:
        resp = client.post(f"{origin}/oauth/device_authorization", json=body)
    if resp.status_code >= 400:
        err = _oauth_error_body(resp)
        raise ClientError(
            f"device authorization failed: {err}",
            code=ErrorCode.UNAUTHENTICATED,
            exit_code=EXIT_AUTH,
            http_status=resp.status_code,
        )
    data = resp.json()
    return DeviceAuthStarted(
        device_code=str(data["device_code"]),
        user_code=str(data["user_code"]),
        verification_uri=str(data.get("verification_uri") or f"{origin}/device"),
        verification_uri_complete=str(
            data.get("verification_uri_complete")
            or f"{origin}/device?code={data['user_code']}"
        ),
        expires_in=int(data.get("expires_in") or 600),
        interval=max(int(data.get("interval") or 2), 1),
    )


def poll_device_token(
    server: str,
    *,
    device_code: str,
) -> TokenBundle | None:
    """Poll once. Return tokens on success, ``None`` if still pending.

    Raises :class:`ClientError` with exit 8 on deny / expire / hard errors.
    """
    origin = server_origin(server)
    with _client() as client:
        resp = client.post(
            f"{origin}/oauth/token",
            json={
                "grant_type": DEVICE_GRANT_TYPE,
                "device_code": device_code,
                "client_id": CLI_CLIENT_ID,
            },
        )
    if resp.status_code == 200:
        return _bundle_from_token_response(resp.json())
    err = _oauth_error_body(resp)
    if err in ("authorization_pending", "slow_down"):
        return None
    if err in ("access_denied", "expired_token", "invalid_grant"):
        raise ClientError(
            f"device grant {err}",
            code=ErrorCode.UNAUTHENTICATED,
            exit_code=EXIT_AUTH,
            http_status=resp.status_code,
        )
    raise ClientError(
        f"token poll failed: {err or resp.text}",
        code=ErrorCode.UNAUTHENTICATED,
        exit_code=EXIT_AUTH,
        http_status=resp.status_code,
    )


def refresh_access_token(server: str, refresh_token: str) -> TokenBundle:
    origin = server_origin(server)
    with _client() as client:
        resp = client.post(
            f"{origin}/oauth/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLI_CLIENT_ID,
            },
        )
    if resp.status_code != 200:
        err = _oauth_error_body(resp)
        raise ClientError(
            f"refresh failed: {err or resp.text}",
            code=ErrorCode.UNAUTHENTICATED,
            exit_code=EXIT_AUTH,
            http_status=resp.status_code,
        )
    return _bundle_from_token_response(resp.json())


def revoke_token(server: str, token: str) -> None:
    """Best-effort revoke (RFC 7009). Ignores network / 4xx after attempt."""
    if not token.strip():
        return
    origin = server_origin(server)
    try:
        with _client(timeout=10.0) as client:
            client.post(
                f"{origin}/oauth/revoke",
                json={"token": token},
            )
    except Exception:
        return


def _bundle_from_token_response(data: dict[str, Any]) -> TokenBundle:
    access = str(data.get("access_token") or "")
    if not access:
        raise ClientError(
            "token response missing access_token",
            code=ErrorCode.UNAUTHENTICATED,
            exit_code=EXIT_AUTH,
        )
    expires_in = int(data.get("expires_in") or 900)
    expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return TokenBundle(
        access_token=access,
        refresh_token=str(data.get("refresh_token") or ""),
        expires_at=expires_at,
        token_type=str(data.get("token_type") or "Bearer"),
        agent_id=str(data.get("agent_id") or ""),
    )


def _oauth_error_body(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        return resp.text.strip()[:200]
    if isinstance(body, dict):
        return str(body.get("error") or body.get("detail") or "")
    return str(body)


def looks_like_api_url(url: str) -> bool:
    """True when ``url`` has a scheme+host suitable as a profile server."""
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)
