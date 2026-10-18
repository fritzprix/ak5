"""Workspace agent registry + credential issuance (identity.md §4.2 / #27)."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.server.models.actor import Actor
from ak5.server.models.workspace import Agent, AgentCredential
from ak5.server.services.workspaces import WorkspaceServiceError

_AGENT_ROLES = frozenset(
    {Role.TRIAGE.value, Role.WORKER.value, Role.GATEKEEPER.value}
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _caps_json(capabilities: list[str] | None) -> str:
    return json.dumps([str(c) for c in (capabilities or [])])


def _scope_json(board_scope: list[str] | None) -> str:
    return json.dumps([str(b) for b in (board_scope or [])])


async def list_workspace_agents(
    db: AsyncSession, workspace_id: str, *, include_disabled: bool = True
) -> list[Agent]:
    stmt = select(Agent).where(Agent.workspace_id == workspace_id).order_by(Agent.name)
    if not include_disabled:
        stmt = stmt.where(Agent.disabled_at.is_(None))
    return list((await db.execute(stmt)).scalars().all())


async def list_agent_credentials(
    db: AsyncSession, agent_id: str
) -> list[AgentCredential]:
    result = await db.execute(
        select(AgentCredential)
        .where(AgentCredential.agent_id == agent_id)
        .order_by(AgentCredential.created_at.desc())
    )
    return list(result.scalars().all())


async def get_workspace_agent(
    db: AsyncSession, *, workspace_id: str, agent_id: str
) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace_id:
        raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Agent not found")
    return agent


async def create_agent_with_credential(
    db: AsyncSession,
    *,
    workspace_id: str,
    name: str,
    workflow_role: str,
    created_by: str,
    capabilities: list[str] | None = None,
    board_scope: list[str] | None = None,
) -> tuple[Agent, AgentCredential, str]:
    """Create agent + first opaque credential. Returns plaintext token once."""
    role = (workflow_role or Role.WORKER.value).strip().lower()
    if role not in _AGENT_ROLES:
        raise WorkspaceServiceError(
            ErrorCode.PAYLOAD_INVALID, f"Invalid workflow_role '{workflow_role}'"
        )
    clean_name = name.strip()
    if not clean_name:
        raise WorkspaceServiceError(ErrorCode.PAYLOAD_INVALID, "Agent name required")

    agent_id = f"agt_{uuid.uuid4().hex[:16]}"
    caps = _caps_json(capabilities)
    scope = _scope_json(board_scope)
    agent = Agent(
        agent_id=agent_id,
        workspace_id=workspace_id,
        name=clean_name,
        workflow_role=role,
        capabilities=caps,
        board_scope=scope,
        created_by=created_by,
        disabled_at=None,
    )
    db.add(agent)
    db.add(
        Actor(
            actor_id=agent_id,
            actor_type="agent",
            name=clean_name,
            role=role,
            capabilities=caps,
            status="idle",
        )
    )
    cred, plaintext = await _issue_credential(db, agent_id=agent_id, board_scope=scope)
    await db.flush()
    return agent, cred, plaintext


async def issue_credential(
    db: AsyncSession,
    *,
    workspace_id: str,
    agent_id: str,
    board_scope: list[str] | None = None,
) -> tuple[AgentCredential, str]:
    agent = await get_workspace_agent(db, workspace_id=workspace_id, agent_id=agent_id)
    if agent.disabled_at is not None:
        raise WorkspaceServiceError(ErrorCode.ROLE_FORBIDDEN, "Agent is disabled")
    scope = (
        _scope_json(board_scope)
        if board_scope is not None
        else (agent.board_scope or "[]")
    )
    cred, plaintext = await _issue_credential(db, agent_id=agent_id, board_scope=scope)
    await db.flush()
    return cred, plaintext


async def _issue_credential(
    db: AsyncSession, *, agent_id: str, board_scope: str
) -> tuple[AgentCredential, str]:
    plaintext = f"ak5_agt_{secrets.token_urlsafe(32)}"
    cred = AgentCredential(
        credential_id=f"cred_{uuid.uuid4().hex[:16]}",
        agent_id=agent_id,
        token_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
        board_scope=board_scope,
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
    )
    db.add(cred)
    return cred, plaintext


async def disable_agent(
    db: AsyncSession, *, workspace_id: str, agent_id: str
) -> Agent:
    agent = await get_workspace_agent(db, workspace_id=workspace_id, agent_id=agent_id)
    if agent.disabled_at is None:
        agent.disabled_at = _now()
        await db.flush()
    return agent


async def revoke_credential(
    db: AsyncSession,
    *,
    workspace_id: str,
    agent_id: str,
    credential_id: str,
) -> AgentCredential:
    await get_workspace_agent(db, workspace_id=workspace_id, agent_id=agent_id)
    cred = await db.get(AgentCredential, credential_id)
    if cred is None or cred.agent_id != agent_id:
        raise WorkspaceServiceError(ErrorCode.NOT_FOUND, "Credential not found")
    if cred.revoked_at is None:
        cred.revoked_at = _now()
        await db.flush()
    return cred


def parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def agent_to_dict(agent: Agent, credentials: list[AgentCredential]) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "workspace_id": agent.workspace_id,
        "name": agent.name,
        "workflow_role": agent.workflow_role,
        "capabilities": agent.capability_list,
        "board_scope": agent.board_scope_list,
        "created_by": agent.created_by,
        "disabled_at": agent.disabled_at,
        "credentials": [
            {
                "credential_id": c.credential_id,
                "board_scope": parse_json_list(c.board_scope),
                "expires_at": c.expires_at,
                "revoked_at": c.revoked_at,
                "last_used_at": c.last_used_at,
            }
            for c in credentials
        ],
    }
