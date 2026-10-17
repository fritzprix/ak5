"""Shared helpers for workflow v2 ticket creates in tests."""

from __future__ import annotations

from typing import Any


def board_ticket(
    title: str,
    *,
    board_id: str = "proj-core-engine",
    spec: str | None = None,
    acceptance_criteria: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """T2 create_direct payload (column_id ignored if present)."""
    payload: dict[str, Any] = {
        "title": title,
        "board_id": board_id,
        "spec": spec or title,
        "acceptance_criteria": acceptance_criteria or ["done"],
    }
    payload.update(extra)
    return payload


def inbox_ticket(title: str, *, original_request: str | None = None, **extra: Any) -> dict[str, Any]:
    """T1 inbox create payload."""
    payload: dict[str, Any] = {
        "title": title,
        "original_request": original_request or title,
    }
    payload.update(extra)
    return payload
