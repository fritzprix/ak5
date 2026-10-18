"""Workspace query scoping (identity.md §7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select

from ak5.server.auth.principal import Principal

# Models that must carry workspace_id (issue #23 §1). Missing column → fail closed.
_TENANT_MODEL_NAMES: frozenset[str] = frozenset(
    {
        "Board",
        "Column",
        "Ticket",
        "TicketRevision",
        "TicketReview",
        "TicketComment",
        "TicketAttachment",
        "TicketPostmortem",
        "BoardMember",
        "Event",
        "Subscription",
        "IdempotencyKey",
        "AuditLog",
        # Global watermark — fail-closed until explicitly scoped per workspace.
        "EventLogMeta",
    }
)


class ScopeError(TypeError):
    """Raised when a tenant model is queried without a ``workspace_id`` column."""


def scoped(stmt: Select[Any], principal: Principal, model: type[Any]) -> Select[Any]:
    """Restrict a select to the principal's workspace.

    Tenant models listed in issue #23 must define ``workspace_id``; otherwise
    this raises :class:`ScopeError` (fail-closed). Non-tenant models pass through.
    """
    column = getattr(model, "workspace_id", None)
    name = getattr(model, "__name__", type(model).__name__)
    if column is None:
        if name in _TENANT_MODEL_NAMES:
            raise ScopeError(
                f"{name} is a tenant model but has no workspace_id column; "
                "refuse unscoped query"
            )
        return stmt
    return stmt.where(column == principal.workspace_id)
