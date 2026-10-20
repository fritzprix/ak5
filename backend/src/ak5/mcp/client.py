"""Thin HTTP client used by MCP tools — mirrors REST verbs (cli.md §0)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from ak5.contracts.errors import ErrorCode, error_body
from ak5.contracts.payloads import Deliverable, Postmortem, RoutePlan, Verdict
from ak5.contracts.stages import BlockedKind
from ak5.security import identify_headers_from_env


class AK5APIError(Exception):
    """Raised when the server returns a v2 error envelope (``ErrorCode`` exposed)."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(f"{code}: {message}")

    def envelope(self) -> dict[str, Any]:
        return error_body(self.code, self.message, details=self.details)


_DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1"


def _default_api_url() -> str:
    """``AK5_API_URL`` > active profile's server > local default (same order as the CLI)."""
    env = os.getenv("AK5_API_URL", "").strip()
    if env:
        return env
    try:
        from ak5.daemon.profiles import get_profile, resolve_profile_name

        name = resolve_profile_name(None)
        profile = get_profile(name) if name else None
        if profile is not None and profile.server.strip():
            return profile.server.strip()
    except Exception:  # noqa: BLE001 — no profile store: fall back to local
        pass
    return _DEFAULT_API_URL


class AK5Client:
    def __init__(
        self,
        base_url: str | None = None,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> None:
        self.base_url = base_url or _default_api_url()
        self.actor_id = actor_id or os.getenv("AK5_ACTOR_ID", "agent_orchestrator")
        self.actor_role = actor_role or os.getenv("AK5_ACTOR_ROLE", "worker")
        self._token: str | None = os.getenv("AK5_ACTOR_TOKEN")
        # Tokens pinned via AK5_ACTOR_TOKEN / use_bearer are never re-resolved.
        self._token_pinned = self._token is not None
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    def use_bearer(self, token: str, *, actor_id: str | None = None, actor_role: str | None = None) -> None:
        """Switch the active JWT (and optional identity hints) without re-identify."""
        self._token = token
        self._token_pinned = True
        if actor_id is not None:
            self.actor_id = actor_id
        if actor_role is not None:
            self.actor_role = actor_role

    def _api(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _resolve_profile_token() -> str | None:
        """``AK5_TOKEN`` / ``AK5_PROFILE`` → daemon ``/v1/token`` (client.md §7, #35)."""
        from ak5.client.auth import resolve_access_token
        from ak5.client.errors import ClientError

        try:
            return resolve_access_token()
        except ClientError as exc:
            try:
                code = ErrorCode(str(exc.code)) if exc.code else ErrorCode.DAEMON_UNAVAILABLE
            except ValueError:
                code = ErrorCode.DAEMON_UNAVAILABLE
            raise AK5APIError(code, exc.message, details=getattr(exc, "details", None)) from exc

    async def ensure_token(self) -> str:
        if self._token:
            return self._token
        # Same resolution as the CLI; IPC is blocking, so keep it off the event loop.
        token = await asyncio.to_thread(self._resolve_profile_token)
        if token:
            self._token = token
            return token
        # Dev-only fallback: /auth/identify answers only with AK5_DEV_INSECURE_IDENTIFY.
        resp = await self._http.post(
            self._api("auth/identify"),
            json={
                "actor_id": self.actor_id,
                "actor_type": "agent",
                "name": f"Agent {self.actor_id}",
                "role": self.actor_role,
                "description": "Autonomous AI Agent connected via MCP",
                "capabilities": ["orchestration", "delegation", "task-execution"],
            },
            headers=identify_headers_from_env(),
        )
        self._raise_for_response(resp)
        self._token = resp.json()["access_token"]
        return self._token

    async def _headers(self) -> dict[str, str]:
        token = await self.ensure_token()
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _raise_for_response(resp: httpx.Response) -> None:
        """Raise :class:`AK5APIError` for v2 envelopes; otherwise ``HTTPStatusError``."""
        if resp.is_success or resp.status_code == 204:
            return
        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            return
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and "code" in err:
            try:
                code = ErrorCode(err["code"])
            except ValueError:
                resp.raise_for_status()
                return
            raise AK5APIError(
                code,
                str(err.get("message") or code.value),
                details=err.get("details") if isinstance(err.get("details"), dict) else {},
                status_code=resp.status_code,
            )
        resp.raise_for_status()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = await self._headers()
        resp = await self._http.request(
            method,
            self._api(path),
            headers=headers,
            json=json,
            params=params,
        )
        if resp.status_code == 401 and not self._token_pinned:
            # Daemon-issued user JWTs are short-lived: re-resolve once (#35).
            self._token = None
            headers = await self._headers()
            resp = await self._http.request(
                method,
                self._api(path),
                headers=headers,
                json=json,
                params=params,
            )
        self._raise_for_response(resp)
        return resp

    # ------------------------------------------------------------------
    # v2 verbs (cli.md §4.3 / §4.4)
    # ------------------------------------------------------------------

    async def next_ticket(
        self,
        board_id: str,
        *,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        if capabilities:
            params["capabilities"] = ",".join(capabilities)
        resp = await self._request("GET", f"boards/{board_id}/next", params=params or None)
        if resp.status_code == 204:
            return None
        return resp.json()

    async def claim(
        self,
        ticket_id: str,
        *,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if capabilities is not None:
            body["capabilities"] = capabilities
        resp = await self._request("POST", f"tickets/{ticket_id}/claim", json=body)
        return resp.json()

    async def heartbeat(self, ticket_id: str, *, claim_token: str) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/heartbeat",
            json={"claim_token": claim_token},
        )
        return resp.json()

    async def release(self, ticket_id: str, *, claim_token: str, reason: str) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/release",
            json={"claim_token": claim_token, "reason": reason},
        )
        return resp.json()

    async def submit(
        self,
        ticket_id: str,
        *,
        claim_token: str,
        deliverable: Deliverable | dict[str, Any],
    ) -> dict[str, Any]:
        payload = (
            deliverable.model_dump(by_alias=True, mode="json")
            if isinstance(deliverable, Deliverable)
            else deliverable
        )
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/submit",
            json={"claim_token": claim_token, "deliverable": payload},
        )
        return resp.json()

    async def bounce(self, ticket_id: str, *, claim_token: str, reason: str) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/bounce",
            json={"claim_token": claim_token, "reason": reason},
        )
        return resp.json()

    async def route(
        self,
        ticket_id: str,
        *,
        claim_token: str,
        plan: RoutePlan | dict[str, Any],
    ) -> dict[str, Any]:
        payload = plan.model_dump(mode="json") if isinstance(plan, RoutePlan) else plan
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/route",
            json={"claim_token": claim_token, "plan": payload},
        )
        return resp.json()

    async def verdict(
        self,
        ticket_id: str,
        *,
        verdict: Verdict | dict[str, Any],
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        body = verdict.model_dump(by_alias=True, mode="json") if isinstance(verdict, Verdict) else verdict
        payload: dict[str, Any] = {"verdict": body}
        if claim_token is not None:
            payload["claim_token"] = claim_token
        resp = await self._request("POST", f"tickets/{ticket_id}/verdict", json=payload)
        return resp.json()

    async def postmortem(
        self,
        ticket_id: str,
        *,
        claim_token: str,
        postmortem: Postmortem | dict[str, Any],
    ) -> dict[str, Any]:
        body = (
            postmortem.model_dump(mode="json") if isinstance(postmortem, Postmortem) else postmortem
        )
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/postmortem",
            json={"claim_token": claim_token, "postmortem": body},
        )
        return resp.json()

    async def block(
        self,
        ticket_id: str,
        *,
        kind: BlockedKind | str,
        reason: str,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        kind_value = kind.value if isinstance(kind, BlockedKind) else kind
        payload: dict[str, Any] = {"kind": kind_value, "reason": reason}
        if claim_token is not None:
            payload["claim_token"] = claim_token
        resp = await self._request("POST", f"tickets/{ticket_id}/block", json=payload)
        return resp.json()

    async def unblock(self, ticket_id: str, *, resolution: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if resolution is not None:
            body["resolution"] = resolution
        resp = await self._request("POST", f"tickets/{ticket_id}/unblock", json=body)
        return resp.json()

    async def reopen(self, ticket_id: str, *, reason: str) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/reopen",
            json={"reason": reason},
        )
        return resp.json()

    async def reroute(
        self,
        ticket_id: str,
        *,
        reason: str,
        to_inbox: bool = False,
        target_board_id: str | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            f"tickets/{ticket_id}/reroute",
            json={
                "reason": reason,
                "to_inbox": to_inbox,
                "target_board_id": target_board_id,
            },
        )
        return resp.json()

    async def history(self, ticket_id: str) -> dict[str, Any]:
        resp = await self._request("GET", f"tickets/{ticket_id}/history")
        return resp.json()

    async def create(
        self,
        title: str,
        *,
        board_id: str | None = None,
        original_request: str | None = None,
        spec: str | None = None,
        acceptance_criteria: list[str] | None = None,
        description: str | None = None,
        priority: str = "medium",
        labels: list[str] | None = None,
        max_attempts: int | None = None,
        parent_ticket_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "priority": priority,
            "labels": labels or [],
        }
        if board_id is not None:
            payload["board_id"] = board_id
        if original_request is not None:
            payload["original_request"] = original_request
        elif board_id is None:
            payload["original_request"] = title
        if spec is not None:
            payload["spec"] = spec
        if acceptance_criteria is not None:
            payload["acceptance_criteria"] = acceptance_criteria
        if description is not None:
            payload["description"] = description
        if max_attempts is not None:
            payload["max_attempts"] = max_attempts
        if parent_ticket_id is not None:
            payload["parent_ticket_id"] = parent_ticket_id
        resp = await self._request("POST", "tickets", json=payload)
        return resp.json()

    # ------------------------------------------------------------------
    # Discovery / legacy helpers (non-conflicting or deprecated wrappers)
    # ------------------------------------------------------------------

    async def list_boards(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "boards")
        return resp.json()

    async def list_available_agents(
        self, capability: str | None = None, search_query: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if capability:
            params["capability"] = capability
        if search_query:
            params["query"] = search_query
        resp = await self._request("GET", "actors/discovery", params=params or None)
        return resp.json()

    async def get_ticket_context(self, ticket_id: str) -> dict[str, Any]:
        resp = await self._request("GET", f"tickets/{ticket_id}")
        return resp.json()

    async def delegate(
        self,
        parent_ticket_id: str,
        *,
        title: str,
        description: str | None = None,
        acceptance_criteria: list[str] | None = None,
        priority: str = "medium",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Parented create_direct (``POST /tickets/{id}/delegate``)."""
        payload: dict[str, Any] = {
            "subtask_title": title,
            "subtask_description": description or "",
            "priority": priority,
            "labels": labels if labels is not None else ["delegated", "subtask"],
        }
        if acceptance_criteria is not None:
            payload["acceptance_criteria"] = acceptance_criteria
        resp = await self._request("POST", f"tickets/{parent_ticket_id}/delegate", json=payload)
        return resp.json()

    async def search_tickets(
        self,
        board_id: str | None = None,
        query: str | None = None,
        is_archived: bool | None = False,
        include_all: bool = False,
        status: str | None = None,
        stage: str | None = None,
        assigned_to: str | None = None,
        labels: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if board_id:
            params["board_id"] = board_id
        if include_all or is_archived is None:
            params["include_all"] = "true"
        else:
            params["is_archived"] = "true" if is_archived else "false"
        if query:
            params["q"] = query
        if status:
            params["status"] = status
        if stage:
            params["stage"] = stage
        if assigned_to:
            params["assigned_to"] = assigned_to
        if labels:
            params["labels"] = labels
        resp = await self._request("GET", "tickets", params=params)
        return resp.json()

    async def archive_ticket(self, ticket_id: str, unarchive: bool = False) -> dict[str, Any]:
        action = "unarchive" if unarchive else "archive"
        resp = await self._request("POST", f"tickets/{ticket_id}/{action}")
        return resp.json()

    async def close(self) -> None:
        await self._http.aclose()
