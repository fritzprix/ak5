"""Board templates (workflow.md §2.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    stage: str
    position: int
    wip_limit: int = 0


@dataclass(frozen=True)
class BoardTemplate:
    name: str
    columns: tuple[ColumnSpec, ...]
    max_attempts: int = 3
    # Reserved for later waves (capability / acceptance defaults).
    worker_capabilities: tuple[str, ...] = ()
    acceptance_criteria_defaults: tuple[str, ...] = ()


DEFAULT_TEMPLATE = BoardTemplate(
    name="default",
    max_attempts=3,
    columns=(
        ColumnSpec(name="Backlog", stage="backlog", position=1, wip_limit=0),
        ColumnSpec(name="In Progress", stage="in_progress", position=2, wip_limit=3),
        ColumnSpec(name="Agent Review", stage="agent_review", position=3, wip_limit=3),
        ColumnSpec(
            name="Postmortem Pending",
            stage="postmortem_pending",
            position=4,
            wip_limit=0,
        ),
        ColumnSpec(name="Human Review", stage="human_review", position=5, wip_limit=0),
        ColumnSpec(name="Done", stage="done", position=6, wip_limit=0),
    ),
)

INBOX_TEMPLATE = BoardTemplate(
    name="inbox",
    max_attempts=3,
    columns=(
        ColumnSpec(name="Unclassified", stage="unclassified", position=1, wip_limit=0),
    ),
)

TEMPLATES: dict[str, BoardTemplate] = {
    DEFAULT_TEMPLATE.name: DEFAULT_TEMPLATE,
    INBOX_TEMPLATE.name: INBOX_TEMPLATE,
}


def get_template(name: str) -> BoardTemplate:
    """Return a named board template or raise ``KeyError``."""
    try:
        return TEMPLATES[name]
    except KeyError as exc:
        known = ", ".join(sorted(TEMPLATES))
        raise KeyError(f"Unknown board template '{name}'. Known: {known}") from exc


def template_column_dicts(template: BoardTemplate) -> list[dict[str, Any]]:
    """Serialize template columns for board seeding / API helpers."""
    return [
        {
            "name": col.name,
            "stage": col.stage,
            "position": col.position,
            "wip_limit": col.wip_limit,
        }
        for col in template.columns
    ]
