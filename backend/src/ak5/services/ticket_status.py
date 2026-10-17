"""Derive ticket ``status`` for API backward compatibility (workflow.md §2).

``status`` is a derived field: done → done, blocked → blocked, claimed →
in_progress, else → open. Callers should persist the derived value when
mutating claim / blocked / stage state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol


class _TicketLike(Protocol):
    blocked_kind: str | None
    blocked_by: str | None
    claim_token: str | None
    lease_expires_at: datetime | None


# Legacy mapping used by v1 move/create paths until transition API (#10).
STAGE_TO_STATUS: dict[str, str] = {
    "unclassified": "open",
    "backlog": "open",
    "open": "open",  # legacy stage name (pre-migration)
    "in_progress": "in_progress",
    "agent_review": "in_progress",
    "review": "in_progress",  # legacy stage name (pre-migration)
    "postmortem_pending": "in_progress",
    "human_review": "in_progress",
    "done": "done",
}


def status_for_column_stage(stage: str) -> str:
    """Return the workflow status implied by a column stage (v1 compatibility)."""
    return STAGE_TO_STATUS.get(stage, "open")


def _resolve_stage(ticket: Any) -> str | None:
    stage = getattr(ticket, "stage", None)
    if stage is not None:
        return str(stage)
    column = getattr(ticket, "column", None)
    if column is not None:
        col_stage = getattr(column, "stage", None)
        if col_stage is not None:
            return str(col_stage)
    return None


def _is_claimed(ticket: _TicketLike, *, now: datetime | None = None) -> bool:
    if not ticket.claim_token:
        return False
    expires = ticket.lease_expires_at
    if expires is None:
        return True
    ref = now or datetime.now(UTC)
    expires_cmp = expires.replace(tzinfo=UTC) if expires.tzinfo is None else expires
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    return expires_cmp > ref


def _is_blocked(ticket: _TicketLike) -> bool:
    return bool(ticket.blocked_kind) or bool(ticket.blocked_by)


def derive_status(ticket: _TicketLike | Any, *, now: datetime | None = None) -> str:
    """Derive API ``status`` from stage / claim / blocked overlays (workflow.md §2).

    Priority: stage ``done`` → ``done``; blocked → ``blocked``; claimed →
    ``in_progress``; else → ``open``.
    """
    stage = _resolve_stage(ticket)
    if stage == "done":
        return "done"
    if _is_blocked(ticket):
        return "blocked"
    if _is_claimed(ticket, now=now):
        return "in_progress"
    return "open"
