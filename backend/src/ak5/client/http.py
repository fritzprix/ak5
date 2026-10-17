"""httpx wrapper: version negotiation, error envelope → exit codes (cli.md §2–§3)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from ak5.client.errors import (
    EXIT_AUTH,
    EXIT_EMPTY,
    EXIT_GENERIC,
    EXIT_VERSION,
    ClientError,
    exit_code_for_error,
)
from ak5.contracts.errors import ErrorCode
from ak5.contracts.version import API_VERSION, MIN_CLIENT_VERSION

# Protocol version this client speaks (independent of PyPI package version).
CLIENT_PROTOCOL_VERSION = MIN_CLIENT_VERSION

_SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)"  # major.minor.patch
)


def _parse_semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.match(value.strip())
    if not match:
        return (0, 0, 0)
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def versions_compatible(
    *,
    server_api_version: str,
    server_min_client: str,
    client_version: str = CLIENT_PROTOCOL_VERSION,
    expected_api: str = API_VERSION,
) -> bool:
    """True when server API matches and client >= server min_client."""
    if str(server_api_version).strip() != str(expected_api).strip():
        return False
    return _parse_semver(client_version) >= _parse_semver(server_min_client)


def api_root_from_url(api_url: str) -> str:
    """Derive server origin from ``…/api/v1`` for ``GET /meta``."""
    parsed = urlparse(api_url.rstrip("/"))
    path = parsed.path or ""
    if path.endswith("/api/v1"):
        path = path[: -len("/api/v1")] or ""
    elif path.endswith("/api/v1/"):
        path = path[: -len("/api/v1/")] or ""
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/") or (
        f"{parsed.scheme}://{parsed.netloc}"
    )


def normalize_api_url(api_url: str) -> str:
    return api_url.rstrip("/")


class AK5Client:
    """Thin REST client. Optionally inject ``transport`` for ASGI tests."""

    def __init__(
        self,
        api_url: str,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        skip_version_check: bool = False,
        client_version: str = CLIENT_PROTOCOL_VERSION,
    ) -> None:
        self.api_url = normalize_api_url(api_url)
        self.root_url = api_root_from_url(self.api_url)
        self.client_version = client_version
        self._skip_version_check = skip_version_check
        self._version_checked = False
        # Shared test transports must not be closed by the client.
        self._owns_transport = transport is None
        hdrs = dict(headers or {})
        if token and "Authorization" not in hdrs:
            hdrs["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers=hdrs,
            base_url=self.root_url or None,
        )

    def close(self) -> None:
        if not self._owns_transport:
            return
        self._client.close()

    def __enter__(self) -> AK5Client:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ---- version negotiation -------------------------------------------------

    def ensure_version(self) -> dict[str, Any]:
        """Fetch ``/meta`` once; raise ClientError(exit 7) on mismatch."""
        if self._skip_version_check:
            self._version_checked = True
            return {}
        if self._version_checked:
            return {}
        try:
            resp = self._client.get("/meta")
        except httpx.HTTPError as exc:
            raise ClientError(
                f"Failed to reach AK5 server for version check: {exc}",
                exit_code=EXIT_GENERIC,
            ) from exc

        if resp.status_code == 401:
            raise ClientError(
                "Authentication required",
                code=ErrorCode.UNAUTHENTICATED,
                exit_code=EXIT_AUTH,
                http_status=401,
            )

        if resp.status_code >= 400:
            raise ClientError(
                f"Version check failed (HTTP {resp.status_code})",
                exit_code=EXIT_GENERIC,
                http_status=resp.status_code,
            )

        meta = resp.json()
        api_ver = str(meta.get("api_version", ""))
        min_client = str(meta.get("min_client", "0.0.0"))
        if not versions_compatible(
            server_api_version=api_ver,
            server_min_client=min_client,
            client_version=self.client_version,
        ):
            raise ClientError(
                f"Version mismatch: server api_version={api_ver!r} "
                f"min_client={min_client!r}, client={self.client_version!r} "
                f"(expected api_version={API_VERSION!r})",
                code=ErrorCode.VERSION_MISMATCH,
                exit_code=EXIT_VERSION,
                details={
                    "api_version": api_ver,
                    "min_client": min_client,
                    "client_version": self.client_version,
                },
            )
        self._version_checked = True
        return meta if isinstance(meta, dict) else {}

    # ---- low-level request ---------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        allow_204: bool = False,
        expect_empty: bool = False,
    ) -> Any:
        """Perform an authenticated API call under ``/api/v1``.

        ``path`` is relative to the API root (e.g. ``/tickets`` or ``tickets``).
        """
        self.ensure_version()
        url = path if path.startswith("http") else self._api_path(path)
        try:
            resp = self._client.request(method, url, json=json, params=params)
        except httpx.HTTPError as exc:
            raise ClientError(
                f"Network error: {exc}",
                exit_code=EXIT_GENERIC,
            ) from exc

        return self._handle_response(resp, allow_204=allow_204, expect_empty=expect_empty)

    def get(self, path: str, *, params: dict[str, Any] | None = None, allow_204: bool = False) -> Any:
        return self.request("GET", path, params=params, allow_204=allow_204)

    def post(self, path: str, *, json: Any | None = None, params: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json=json, params=params)

    def patch(self, path: str, *, json: Any | None = None) -> Any:
        return self.request("PATCH", path, json=json)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def _api_path(self, path: str) -> str:
        p = path if path.startswith("/") else f"/{path}"
        # Prefer absolute URL so ASGI transport (base_url = origin) resolves correctly.
        return f"{self.api_url}{p}"

    def _handle_response(
        self,
        resp: httpx.Response,
        *,
        allow_204: bool,
        expect_empty: bool,
    ) -> Any:
        if resp.status_code == 401:
            raise ClientError(
                "Authentication required or session expired",
                code=ErrorCode.UNAUTHENTICATED,
                exit_code=EXIT_AUTH,
                http_status=401,
            )

        if resp.status_code == 204:
            if allow_204 or expect_empty:
                raise ClientError(
                    "No work available",
                    exit_code=EXIT_EMPTY,
                    details={"empty": True},
                )
            return None

        if resp.status_code >= 400:
            raise self._error_from_response(resp)

        if not resp.content:
            return None
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type or resp.content[:1] in (b"{", b"["):
            return resp.json()
        return resp.text

    def _error_from_response(self, resp: httpx.Response) -> ClientError:
        code: ErrorCode | str | None = None
        message = f"HTTP {resp.status_code}"
        details: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:
            body = None

        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                raw_code = err.get("code")
                message = str(err.get("message") or message)
                details = err.get("details") if isinstance(err.get("details"), dict) else {}
                if isinstance(raw_code, str):
                    try:
                        code = ErrorCode(raw_code)
                    except ValueError:
                        code = raw_code
            elif "detail" in body:
                detail = body["detail"]
                if isinstance(detail, dict) and "code" in detail:
                    raw_code = detail.get("code")
                    message = str(detail.get("message") or message)
                    details = detail.get("details") if isinstance(detail.get("details"), dict) else {}
                    if isinstance(raw_code, str):
                        try:
                            code = ErrorCode(raw_code)
                        except ValueError:
                            code = raw_code
                else:
                    message = str(detail)
                    if resp.status_code == 422 and code is None:
                        code = ErrorCode.PAYLOAD_INVALID
                        details = {"detail": detail} if not isinstance(detail, dict) else detail

        exit_code = exit_code_for_error(code, http_status=resp.status_code)
        return ClientError(
            message,
            code=code,
            exit_code=exit_code,
            details=details if isinstance(details, dict) else {},
            http_status=resp.status_code,
        )

    # ---- convenience workflow helpers ----------------------------------------

    def next_ticket(
        self,
        board_id: str,
        *,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if capabilities:
            params["capabilities"] = ",".join(capabilities)
        return self.get(f"/boards/{board_id}/next", params=params or None, allow_204=True)

    def create_ticket(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/tickets", json=payload)

    def claim_ticket(
        self,
        ticket_id: str,
        *,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if capabilities is not None:
            body["capabilities"] = capabilities
        return self.post(f"/tickets/{ticket_id}/claim", json=body or None)

    def heartbeat(self, ticket_id: str, *, claim_token: str) -> dict[str, Any]:
        return self.post(
            f"/tickets/{ticket_id}/heartbeat",
            json={"claim_token": claim_token},
        )

    def release(self, ticket_id: str, *, claim_token: str, reason: str) -> dict[str, Any]:
        return self.post(
            f"/tickets/{ticket_id}/release",
            json={"claim_token": claim_token, "reason": reason},
        )

    def submit(
        self,
        ticket_id: str,
        *,
        claim_token: str,
        deliverable: dict[str, Any],
    ) -> dict[str, Any]:
        return self.post(
            f"/tickets/{ticket_id}/submit",
            json={"claim_token": claim_token, "deliverable": deliverable},
        )

    def bounce(self, ticket_id: str, *, claim_token: str, reason: str) -> dict[str, Any]:
        return self.post(
            f"/tickets/{ticket_id}/bounce",
            json={"claim_token": claim_token, "reason": reason},
        )

    def route(
        self,
        ticket_id: str,
        *,
        claim_token: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        return self.post(
            f"/tickets/{ticket_id}/route",
            json={"claim_token": claim_token, "plan": plan},
        )

    def verdict(
        self,
        ticket_id: str,
        *,
        claim_token: str | None,
        verdict: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"verdict": verdict}
        if claim_token is not None:
            body["claim_token"] = claim_token
        return self.post(f"/tickets/{ticket_id}/verdict", json=body)

    def postmortem(
        self,
        ticket_id: str,
        *,
        claim_token: str,
        postmortem: dict[str, Any],
    ) -> dict[str, Any]:
        return self.post(
            f"/tickets/{ticket_id}/postmortem",
            json={"claim_token": claim_token, "postmortem": postmortem},
        )

    def block(
        self,
        ticket_id: str,
        *,
        kind: str,
        reason: str,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "reason": reason}
        if claim_token is not None:
            body["claim_token"] = claim_token
        return self.post(f"/tickets/{ticket_id}/block", json=body)

    def unblock(self, ticket_id: str, *, resolution: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if resolution is not None:
            body["resolution"] = resolution
        return self.post(f"/tickets/{ticket_id}/unblock", json=body or None)

    def reopen(self, ticket_id: str, *, reason: str) -> dict[str, Any]:
        return self.post(f"/tickets/{ticket_id}/reopen", json={"reason": reason})

    def reroute(
        self,
        ticket_id: str,
        *,
        reason: str,
        to_inbox: bool = False,
        target_board_id: str | None = None,
    ) -> dict[str, Any]:
        return self.post(
            f"/tickets/{ticket_id}/reroute",
            json={
                "reason": reason,
                "to_inbox": to_inbox,
                "target_board_id": target_board_id,
            },
        )

    def history(self, ticket_id: str) -> dict[str, Any]:
        return self.get(f"/tickets/{ticket_id}/history")

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        return self.get(f"/tickets/{ticket_id}")

    def get_board(self, board_id: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.get(f"/boards/{board_id}", params=params)

    def list_boards(self) -> list[dict[str, Any]]:
        data = self.get("/boards")
        return data if isinstance(data, list) else []

    def create_board(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/boards", json=payload)

    def update_board(self, board_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.patch(f"/boards/{board_id}", json=payload)

    def list_templates(self) -> list[dict[str, Any]]:
        data = self.get("/templates")
        return data if isinstance(data, list) else []

    def get_template(self, name: str) -> dict[str, Any]:
        return self.get(f"/templates/{name}")

    def adopt_board(self, board_id: str) -> dict[str, Any]:
        return self.post(f"/boards/{board_id}/claim")

    def move_ticket(self, ticket_id: str, *, target_column_id: str, reason: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"target_column_id": target_column_id}
        if reason is not None:
            body["reason"] = reason
        return self.patch(f"/tickets/{ticket_id}/move", json=body)

    def delegate(self, parent_ticket_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/tickets/{parent_ticket_id}/delegate", json=payload)

    def identify(self, payload: dict[str, Any], *, extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
        """POST /auth/identify — skips version gate only for the identify call itself.

        Version is still checked first so mismatched servers fail early.
        """
        self.ensure_version()
        headers = dict(extra_headers or {})
        try:
            resp = self._client.post(f"{self.api_url}/auth/identify", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ClientError(f"Network error: {exc}", exit_code=EXIT_GENERIC) from exc
        return self._handle_response(resp, allow_204=False, expect_empty=False)
