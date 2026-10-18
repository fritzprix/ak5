"""Derive ticket ``status`` for API backward compatibility (workflow.md §2).

``status`` is a derived field: done → done, blocked → blocked, claimed →
in_progress, else → open. It is not stored; ``Ticket.status`` is a hybrid
property built on :func:`derive_status` (SQL side: ``Ticket._status_expression``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol


class _TicketLike(Protocol):
    blocked_kind: str | None
    blocked_by: str | None
    claim_token: str | None
    lease_expires_at: datetime | None


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
