"""Map Kanban column stages to ticket workflow status.

Column stage is the source of truth for open / in_progress / done.
``blocked`` is an overlay that may apply on any column without moving the ticket.
"""

from __future__ import annotations

STAGE_TO_STATUS: dict[str, str] = {
    "open": "open",
    "in_progress": "in_progress",
    "review": "in_progress",
    "done": "done",
}


def status_for_column_stage(stage: str) -> str:
    """Return the workflow status implied by a column stage."""
    return STAGE_TO_STATUS.get(stage, "open")
