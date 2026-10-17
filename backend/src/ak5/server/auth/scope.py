"""Workspace query scoping (identity.md §7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select

from ak5.server.auth.principal import Principal


def scoped(stmt: Select[Any], principal: Principal, model: type[Any]) -> Select[Any]:
    """Restrict a select to the principal's workspace when the model has ``workspace_id``."""
    column = getattr(model, "workspace_id", None)
    if column is None:
        return stmt
    return stmt.where(column == principal.workspace_id)
