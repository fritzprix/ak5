"""Workspace resource quota enforcement and usage tracking (operations.md §3, #116)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.contracts.stages import Stage
from ak5.server.models.board import Board
from ak5.server.models.column import Column
from ak5.server.models.ticket import Ticket
from ak5.server.models.workspace import Agent, Membership, Workspace, WorkspaceUsage

logger = logging.getLogger(__name__)

QUOTA_KEYS = (
    "max_boards",
    "max_open_tickets",
    "max_attachment_bytes_total",
    "max_members",
    "max_agents",
    "event_retention_days",
)

HOSTED_QUOTA_DEFAULTS: dict[str, int] = {
    "max_boards": 50,
    "max_open_tickets": 10000,
    "max_attachment_bytes_total": 5 * 1024 * 1024 * 1024,  # 5 GiB
    "max_members": 50,
    "max_agents": 50,
    "event_retention_days": 7,
}

ATTACHMENT_BYTES_KEY = "attachment_bytes"



def _inc_quota_rejection(quota: str) -> None:
    try:
        from ak5.server.metrics import quota_rejections_total

        quota_rejections_total.inc(quota=quota)
    except Exception:
        pass


def _upsert_for(dialect: str):
    if dialect == "sqlite":
        return sqlite.insert
    if dialect == "postgresql":
        return postgresql.insert
    raise RuntimeError(f"Workspace usage does not support the {dialect!r} database dialect")


class QuotaExceeded(Exception):
    def __init__(self, quota: str, limit: int, current: int) -> None:
        self.quota = quota
        self.limit = limit
        self.current = current
        super().__init__(f"Workspace quota '{quota}' exceeded: current {current} >= limit {limit}")

    @property
    def status_code(self) -> int:
        return HTTP_STATUS[ErrorCode.QUOTA_EXCEEDED]

    def body(self) -> dict[str, Any]:
        return error_body(
            ErrorCode.QUOTA_EXCEEDED,
            f"Workspace quota '{self.quota}' exceeded",
            details={"quota": self.quota, "limit": self.limit, "current": self.current},
        )

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status_code, content=self.body())

    def http_exception(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail=self.body())


def hosted_profile() -> bool:
    return (os.environ.get("AK5_PROFILE") or "local").strip().lower() == "hosted"


def server_quota_ceilings() -> dict[str, int]:
    raw = (os.environ.get("AK5_QUOTA_CEILING") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
    except Exception:
        logger.warning("Failed to parse AK5_QUOTA_CEILING: %r", raw)
    return {}


def validate_quota_policy_patch(patch: dict[str, Any]) -> None:
    ceilings = server_quota_ceilings()
    for key in QUOTA_KEYS:
        if key in patch:
            val = patch[key]
            if not isinstance(val, int) or val < 0:
                raise ValueError(f"Quota '{key}' must be a non-negative integer")
            if key == "event_retention_days" and val > 30:
                raise ValueError("event_retention_days cannot exceed 30")
            if key in ceilings and ceilings[key] > 0 and val > ceilings[key]:
                raise ValueError(f"Quota '{key}' value {val} exceeds server ceiling {ceilings[key]}")


def get_effective_quotas(policy_input: Workspace | dict | str | None) -> dict[str, int]:
    if isinstance(policy_input, Workspace):
        policy_str = policy_input.policy
    elif isinstance(policy_input, dict):
        policy_str = json.dumps(policy_input)
    else:
        policy_str = policy_input or "{}"

    try:
        policy_data = json.loads(policy_str) if isinstance(policy_str, str) else policy_str
        if not isinstance(policy_data, dict):
            policy_data = {}
    except Exception:
        policy_data = {}

    is_hosted = hosted_profile()
    quotas = {}
    for key in QUOTA_KEYS:
        val = policy_data.get(key)
        if val is not None and isinstance(val, int) and val > 0:
            quotas[key] = val
        elif val == 0:
            quotas[key] = 0
        elif is_hosted:
            quotas[key] = HOSTED_QUOTA_DEFAULTS.get(key, 0)
        else:
            quotas[key] = 0
    return quotas


async def check_board_quota(db: AsyncSession, workspace: Workspace) -> None:
    quotas = get_effective_quotas(workspace)
    limit = quotas.get("max_boards", 0)
    if limit <= 0:
        return
    count = await db.scalar(
        select(func.count())
        .select_from(Board)
        .where(Board.workspace_id == workspace.workspace_id, Board.kind != "inbox")
    )
    count = count or 0
    if count >= limit:
        _inc_quota_rejection("max_boards")
        raise QuotaExceeded("max_boards", limit, count)


async def check_ticket_quota(db: AsyncSession, workspace: Workspace) -> None:
    quotas = get_effective_quotas(workspace)
    limit = quotas.get("max_open_tickets", 0)
    if limit <= 0:
        return
    count = await db.scalar(
        select(func.count())
        .select_from(Ticket)
        .join(Column, Column.column_id == Ticket.column_id)
        .where(
            Ticket.workspace_id == workspace.workspace_id,
            Column.stage != Stage.DONE.value,
            Ticket.is_archived.is_(False),
            Ticket.archived_at.is_(None),
        )
    )
    count = count or 0
    if count >= limit:
        _inc_quota_rejection("max_open_tickets")
        raise QuotaExceeded("max_open_tickets", limit, count)


async def check_attachment_quota(db: AsyncSession, workspace: Workspace, new_bytes: int) -> None:
    quotas = get_effective_quotas(workspace)
    limit = quotas.get("max_attachment_bytes_total", 0)
    if limit <= 0:
        return
    row = await db.get(WorkspaceUsage, (workspace.workspace_id, ATTACHMENT_BYTES_KEY))
    current = row.value if row else 0
    if current + new_bytes > limit:
        _inc_quota_rejection("max_attachment_bytes_total")
        raise QuotaExceeded("max_attachment_bytes_total", limit, current)


async def check_member_quota(db: AsyncSession, workspace: Workspace) -> None:
    quotas = get_effective_quotas(workspace)
    limit = quotas.get("max_members", 0)
    if limit <= 0:
        return
    count = await db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.workspace_id == workspace.workspace_id)
    )
    count = count or 0
    if count >= limit:
        _inc_quota_rejection("max_members")
        raise QuotaExceeded("max_members", limit, count)


async def check_agent_quota(db: AsyncSession, workspace: Workspace) -> None:
    quotas = get_effective_quotas(workspace)
    limit = quotas.get("max_agents", 0)
    if limit <= 0:
        return
    count = await db.scalar(
        select(func.count())
        .select_from(Agent)
        .where(Agent.workspace_id == workspace.workspace_id, Agent.disabled_at.is_(None))
    )
    count = count or 0
    if count >= limit:
        _inc_quota_rejection("max_agents")
        raise QuotaExceeded("max_agents", limit, count)


async def record_attachment_bytes(db: AsyncSession, workspace_id: str, delta_bytes: int) -> int:
    insert = _upsert_for(db.get_bind().dialect.name)
    stmt = (
        insert(WorkspaceUsage)
        .values(
            workspace_id=workspace_id,
            key=ATTACHMENT_BYTES_KEY,
            value=max(0, delta_bytes),
        )
        .on_conflict_do_update(
            index_elements=[WorkspaceUsage.workspace_id, WorkspaceUsage.key],
            set_={"value": func.max(0, WorkspaceUsage.value + delta_bytes)},
        )
        .returning(WorkspaceUsage.value)
    )
    result = await db.execute(stmt)
    new_val = result.scalar_one()
    return int(new_val)


async def get_workspace_usage_summary(db: AsyncSession, workspace: Workspace) -> dict[str, Any]:
    quotas = get_effective_quotas(workspace)

    # 1. Boards count
    board_count = await db.scalar(
        select(func.count())
        .select_from(Board)
        .where(Board.workspace_id == workspace.workspace_id, Board.kind != "inbox")
    ) or 0

    # 2. Open tickets count
    open_tickets_count = await db.scalar(
        select(func.count())
        .select_from(Ticket)
        .join(Column, Column.column_id == Ticket.column_id)
        .where(
            Ticket.workspace_id == workspace.workspace_id,
            Column.stage != Stage.DONE.value,
            Ticket.is_archived.is_(False),
            Ticket.archived_at.is_(None),
        )
    ) or 0

    # 3. Attachment bytes
    usage_row = await db.get(WorkspaceUsage, (workspace.workspace_id, ATTACHMENT_BYTES_KEY))
    attachment_bytes = usage_row.value if usage_row else 0

    # 4. Members count
    members_count = await db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.workspace_id == workspace.workspace_id)
    ) or 0

    # 5. Active agents count
    agents_count = await db.scalar(
        select(func.count())
        .select_from(Agent)
        .where(Agent.workspace_id == workspace.workspace_id, Agent.disabled_at.is_(None))
    ) or 0

    return {
        "max_boards": {"limit": quotas.get("max_boards", 0), "current": board_count},
        "max_open_tickets": {"limit": quotas.get("max_open_tickets", 0), "current": open_tickets_count},
        "max_attachment_bytes_total": {
            "limit": quotas.get("max_attachment_bytes_total", 0),
            "current": attachment_bytes,
        },
        "max_members": {"limit": quotas.get("max_members", 0), "current": members_count},
        "max_agents": {"limit": quotas.get("max_agents", 0), "current": agents_count},
        "event_retention_days": {
            "limit": quotas.get("event_retention_days", 0),
            "current": quotas.get("event_retention_days", 0),
        },
    }
