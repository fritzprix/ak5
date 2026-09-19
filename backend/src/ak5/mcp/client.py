import os
from typing import Any
import httpx


class AK5Client:
    def __init__(
        self,
        base_url: str | None = None,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv("AK5_API_URL", "http://127.0.0.1:8000/api/v1")
        self.actor_id = actor_id or os.getenv("AK5_ACTOR_ID", "agent_orchestrator")
        self.actor_role = actor_role or os.getenv("AK5_ACTOR_ROLE", "Lead Orchestrator")
        self._token: str | None = os.getenv("AK5_ACTOR_TOKEN")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def ensure_token(self) -> str:
        if self._token:
            return self._token
        # Identify/register actor and get token
        resp = await self._http.post(
            f"{self.base_url.rstrip('/')}/auth/identify",
            json={
                "actor_id": self.actor_id,
                "actor_type": "agent",
                "name": f"Agent {self.actor_id}",
                "role": self.actor_role,
                "description": "Autonomous AI Agent connected via MCP",
                "capabilities": ["orchestration", "delegation", "task-execution"],
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    async def _headers(self) -> dict[str, str]:
        token = await self.ensure_token()
        return {"Authorization": f"Bearer {token}"}

    async def list_available_agents(
        self, capability: str | None = None, search_query: str | None = None
    ) -> list[dict[str, Any]]:
        params = {}
        if capability:
            params["capability"] = capability
        if search_query:
            params["query"] = search_query
        resp = await self._http.get(f"{self.base_url.rstrip('/')}/actors/discovery", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_ticket_context(self, ticket_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"{self.base_url.rstrip('/')}/tickets/{ticket_id}")
        resp.raise_for_status()
        return resp.json()

    async def delegate_subtask(
        self,
        parent_ticket_id: str,
        target_agent_id: str,
        title: str,
        description: str,
        priority: str = "medium",
    ) -> dict[str, Any]:
        headers = await self._headers()
        payload = {
            "target_actor_id": target_agent_id,
            "subtask_title": title,
            "subtask_description": description,
            "priority": priority,
            "labels": ["delegated", "subtask"],
        }
        resp = await self._http.post(
            f"{self.base_url.rstrip('/')}/tickets/{parent_ticket_id}/delegate",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def update_ticket_status(
        self,
        ticket_id: str,
        column_name: str,
        status_note: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = await self._headers()
        # Fetch ticket to get board
        ticket = await self.get_ticket_context(ticket_id)
        board_resp = await self._http.get(f"{self.base_url.rstrip('/')}/boards/{ticket['board_id']}")
        board_resp.raise_for_status()
        board_data = board_resp.json()

        # Find target column
        target_col = None
        for col in board_data["columns"]:
            if col["name"].lower() == column_name.lower() or col["stage"].lower() == column_name.lower():
                target_col = col
                break

        if not target_col:
            raise ValueError(f"Column '{column_name}' not found on board '{ticket['board_id']}'")

        # Move ticket to target column
        move_resp = await self._http.patch(
            f"{self.base_url.rstrip('/')}/tickets/{ticket_id}/move",
            json={"target_column_id": target_col["column_id"]},
            headers=headers,
        )
        move_resp.raise_for_status()

        # Add comment / status note
        await self._http.post(
            f"{self.base_url.rstrip('/')}/tickets/{ticket_id}/comments",
            json={
                "content": f"[Status -> {column_name}] {status_note}",
                "is_internal": False,
                "metadata": execution_context or {},
            },
            headers=headers,
        )

        # Update execution_context if provided
        if execution_context:
            await self._http.patch(
                f"{self.base_url.rstrip('/')}/tickets/{ticket_id}",
                json={"execution_context": execution_context},
                headers=headers,
            )

        return await self.get_ticket_context(ticket_id)

    async def report_block(
        self,
        ticket_id: str,
        blocking_reason: str,
        required_actor_id: str | None = None,
    ) -> dict[str, Any]:
        headers = await self._headers()
        # Update ticket status to blocked
        update_payload: dict[str, Any] = {"status": "blocked"}
        resp = await self._http.patch(
            f"{self.base_url.rstrip('/')}/tickets/{ticket_id}",
            json=update_payload,
            headers=headers,
        )
        resp.raise_for_status()

        # Add comment with mention
        mention = f" @{required_actor_id}" if required_actor_id else ""
        content = f"⚠️ [BLOCKED]{mention} Reason: {blocking_reason}"
        await self._http.post(
            f"{self.base_url.rstrip('/')}/tickets/{ticket_id}/comments",
            json={"content": content, "is_internal": False},
            headers=headers,
        )
        return resp.json()

    async def close(self) -> None:
        await self._http.aclose()
