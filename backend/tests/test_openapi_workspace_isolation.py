"""OpenAPI-driven workspace isolation (issue #23 completion criterion).

A principal from ``ws_other`` must not read or write ``ws_local`` resources.
Routes are collected from the live OpenAPI schema so new endpoints are covered.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.bridge import register_bridge
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.main import app
from ak5.server.models.actor import Actor
from ak5.server.models.board import Board
from ak5.server.models.ticket import Ticket
from ak5.server.models.workspace import Membership, User, Workspace
from ak5.server.seed import ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _ensure_other_workspace(db: AsyncSession, *, board_id: str = "board-other-ws") -> str:
    other_ws = "ws_other"
    if await db.get(Workspace, other_ws) is None:
        db.add(Workspace(workspace_id=other_ws, name="Other", policy="{}"))
    other_user = "usr_other"
    if await db.get(User, other_user) is None:
        db.add(User(user_id=other_user, email="o@x", display_name="Other"))
    mem_id = f"mem_{other_ws}_{other_user}"
    if await db.get(Membership, mem_id) is None:
        db.add(
            Membership(
                membership_id=mem_id,
                workspace_id=other_ws,
                user_id=other_user,
                role="owner",
            )
        )
    if await db.get(Board, board_id) is None:
        db.add(
            Board(
                board_id=board_id,
                workspace_id=other_ws,
                name="Secret Board",
                created_by="user_pm",
                kind="standard",
                template="default",
            )
        )
    return other_ws

# Endpoints that are auth public / out of tenancy scope for this suite.
_SKIP_PREFIXES = (
    "/api/auth",
    "/api/v1/auth/identify",
    "/api/v1/auth/device",
    "/health",
    "/meta",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/login",
    "/mcp",
)

# Streaming / long-poll — would hang the suite.
_SKIP_SUFFIXES = (
    "/stream",
    "/wait",
    "/long-poll",
    "/longpoll",
)

# Path-parameter placeholders for seeded / fixture resources in ws_local.
_PATH_VALUES = {
    "board_id": "proj-core-engine",
    "ticket_id": "TK-LOCAL-ISO",
    "actor_id": "user_pm",
    "sub_id": "sub-missing-iso",
    "attachment_id": "att-missing-iso",
    "column_id": "col_todo",
}


def _collect_api_operations() -> list[tuple[str, str, str]]:
    """Return ``(method, path_template, operation_id)`` under ``/api/v1``."""
    schema = app.openapi()
    out: list[tuple[str, str, str]] = []
    for path, methods in schema.get("paths", {}).items():
        if not path.startswith("/api/v1"):
            continue
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if any(path.endswith(s) for s in _SKIP_SUFFIXES):
            continue
        for method, op in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not isinstance(op, dict):
                continue
            out.append((method.upper(), path, str(op.get("operationId") or path)))
    return sorted(out)


def _fill_path(template: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return _PATH_VALUES.get(key, f"missing-{key}")

    return re.sub(r"\{([^}]+)\}", repl, template)


def _is_collection_list(path: str, method: str) -> bool:
    """True for list-style GETs that may return [] instead of 403."""
    if method != "GET":
        return False
    # No path params → collection
    if "{" not in path:
        return True
    return False


@pytest.fixture
async def foreign_ws_headers(test_db: AsyncSession) -> dict[str, str]:
    await ensure_local_tenancy(test_db)
    await _ensure_other_workspace(test_db)
    if await test_db.get(Actor, "usr_other") is None:
        test_db.add(
            Actor(
                actor_id="usr_other",
                actor_type="human",
                name="Other WS Owner",
                role="human",
                capabilities="[]",
                status="idle",
            )
        )
    register_bridge("usr_other", "usr_other")
    # Local ticket for ticket-scoped routes
    if await test_db.get(Ticket, "TK-LOCAL-ISO") is None:
        test_db.add(
            Ticket(
                ticket_id="TK-LOCAL-ISO",
                workspace_id="ws_local",
                board_id="proj-core-engine",
                column_id="col_todo",
                title="Local isolation target",
                description="fixture",
                priority="medium",
                rank="a0",
                created_by="user_pm",
            )
        )
    await test_db.commit()
    token = issue_access_token(
        Principal(
            principal_id="usr_other",
            principal_type="user",
            workspace_id="ws_other",
            workflow_role=Role.HUMAN,
            membership_role="owner",
        )
    )
    return {"Authorization": f"Bearer {token}"}


_OPS = _collect_api_operations()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path_template", "op_id"),
    _OPS,
    ids=[f"{m} {p}" for m, p, _ in _OPS],
)
async def test_foreign_principal_cannot_access_local_resources(
    client: AsyncClient,
    foreign_ws_headers: dict[str, str],
    method: str,
    path_template: str,
    op_id: str,
) -> None:
    path = _fill_path(path_template)
    json_body: dict[str, Any] | None = None
    if method in {"POST", "PUT", "PATCH"}:
        # Minimal bodies; invalid payload still must not succeed as 2xx on cross-ws.
        if path_template.rstrip("/").endswith("/columns"):
            json_body = {
                "column_id": "col-iso-leak",
                "name": "Leak",
                "stage": "backlog",
                "position": 99,
                "wip_limit": 0,
            }
        elif path_template.rstrip("/").endswith("/comments"):
            json_body = {"content": "cross-ws"}
        elif path_template.rstrip("/").endswith("/members"):
            json_body = {"actor_id": "user_pm", "role": "member"}
        elif path_template == "/api/v1/boards" and method == "POST":
            json_body = {"name": "Should not matter", "template": "default"}
        elif path_template == "/api/v1/tickets" and method == "POST":
            json_body = {
                "board_id": "proj-core-engine",
                "title": "cross",
                "spec": "x",
                "acceptance_criteria": ["y"],
            }
        elif path_template == "/api/v1/subscriptions" and method == "POST":
            json_body = {"filter": {"board_id": "proj-core-engine"}, "start": "now"}
        else:
            json_body = {}

    resp = await client.request(
        method,
        path,
        headers=foreign_ws_headers,
        json=json_body,
        timeout=5.0,
    )

    # Auth / validation failures are acceptable; success that exposes or mutates
    # local workspace data is not.
    if resp.status_code in {401, 404, 405, 409, 422, 423}:
        return
    if resp.status_code == 403:
        body = resp.json()
        code = (body.get("error") or {}).get("code") or body.get("detail")
        # Prefer WORKSPACE_FORBIDDEN / ROLE_FORBIDDEN envelopes; plain 403 also OK.
        assert code in {
            "WORKSPACE_FORBIDDEN",
            "ROLE_FORBIDDEN",
            "SYSTEM_BOARD",
            None,
        } or isinstance(code, str)
        return
    if resp.status_code == 204:
        # Empty next-work is OK only if board was accessible — must not happen
        # for cross-ws board next (should be 403). Fail closed.
        if "{board_id}" in path_template or "{ticket_id}" in path_template:
            pytest.fail(f"{method} {path_template} returned 204 for foreign principal")
        return
    if 200 <= resp.status_code < 300:
        # Creating a board in the caller's own workspace is fine.
        if method == "POST" and path_template == "/api/v1/boards":
            return
        # Subscription filtered to a local board must not succeed (403 above).
        if _is_collection_list(path_template, method):
            data = resp.json()
            if isinstance(data, list):
                if path_template.rstrip("/").endswith("/actors") or path_template.rstrip(
                    "/"
                ).endswith("/discovery"):
                    ids = {a.get("actor_id") for a in data if isinstance(a, dict)}
                    assert "user_pm" not in ids
                    assert "agent_image_worker" not in ids
                    assert "agent_code_reviewer" not in ids
                    return
                if path_template.rstrip("/").endswith("/boards"):
                    ids = {b.get("board_id") for b in data if isinstance(b, dict)}
                    assert "proj-core-engine" not in ids
                    assert "inbox" not in ids
                    return
                if path_template.rstrip("/").endswith("/tickets"):
                    ids = {t.get("ticket_id") for t in data if isinstance(t, dict)}
                    assert "TK-LOCAL-ISO" not in ids
                    return
                if path_template.rstrip("/").endswith("/templates"):
                    return
                # Other collections: must not leak local ticket/board ids
                blob = str(data)
                assert "TK-LOCAL-ISO" not in blob
                assert "proj-core-engine" not in blob
                return
            if isinstance(data, dict) and "events" in data:
                assert all(
                    e.get("board_id") != "proj-core-engine" for e in (data["events"] or [])
                )
                return
        # Any other 2xx against a concrete local resource is a tenant leak.
        pytest.fail(
            f"cross-workspace {method} {path} ({op_id}) returned {resp.status_code}: "
            f"{resp.text[:300]}"
        )
