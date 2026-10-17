"""Board templates (workflow.md §2.2) — client-safe registry + optional workspace JSON."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ak5.paths import get_app_data_dir

logger = logging.getLogger(__name__)

_STANDARD_COLUMNS = (
    ("Backlog", "backlog", 1, 0),
    ("In Progress", "in_progress", 2, 3),
    ("Agent Review", "agent_review", 3, 3),
    ("Postmortem Pending", "postmortem_pending", 4, 0),
    ("Human Review", "human_review", 5, 0),
    ("Done", "done", 6, 0),
)


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
    worker_capabilities: tuple[str, ...] = ()
    acceptance_criteria_defaults: tuple[str, ...] = ()
    review_checklist: tuple[str, ...] = ()


def _standard_columns() -> tuple[ColumnSpec, ...]:
    return tuple(
        ColumnSpec(name=n, stage=s, position=p, wip_limit=w)
        for n, s, p, w in _STANDARD_COLUMNS
    )


DEFAULT_TEMPLATE = BoardTemplate(
    name="default",
    max_attempts=3,
    columns=_standard_columns(),
    acceptance_criteria_defaults=("done",),
    review_checklist=("acceptance criteria met",),
)

CODING_TEMPLATE = BoardTemplate(
    name="coding",
    max_attempts=3,
    columns=_standard_columns(),
    worker_capabilities=("python", "git"),
    acceptance_criteria_defaults=("tests pass", "ruff clean", "diff scoped to ticket"),
    review_checklist=(
        "diff reviewed for correctness",
        "tests green",
        "no secrets committed",
    ),
)

RESEARCH_TEMPLATE = BoardTemplate(
    name="research",
    max_attempts=3,
    columns=_standard_columns(),
    worker_capabilities=("research",),
    acceptance_criteria_defaults=("sources cited", "summary complete"),
    review_checklist=(
        "claims verified against sources",
        "sources are credible",
        "open questions listed",
    ),
)

INBOX_TEMPLATE = BoardTemplate(
    name="inbox",
    max_attempts=3,
    columns=(
        ColumnSpec(name="Unclassified", stage="unclassified", position=1, wip_limit=0),
    ),
)

BUILTIN_TEMPLATES: dict[str, BoardTemplate] = {
    DEFAULT_TEMPLATE.name: DEFAULT_TEMPLATE,
    CODING_TEMPLATE.name: CODING_TEMPLATE,
    RESEARCH_TEMPLATE.name: RESEARCH_TEMPLATE,
    INBOX_TEMPLATE.name: INBOX_TEMPLATE,
}

# Backward-compatible alias (built-ins only; prefer list_templates / get_template).
TEMPLATES: dict[str, BoardTemplate] = BUILTIN_TEMPLATES


def workspace_templates_dir() -> Path:
    """``$AK5_DATA_DIR/templates`` — one JSON file per template (``*.json``)."""
    path = get_app_data_dir() / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_column(raw: dict[str, Any], index: int) -> ColumnSpec:
    try:
        return ColumnSpec(
            name=str(raw["name"]),
            stage=str(raw["stage"]),
            position=int(raw.get("position", index + 1)),
            wip_limit=int(raw.get("wip_limit", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid column spec: {raw!r}") from exc


def _template_from_mapping(data: dict[str, Any], *, fallback_name: str) -> BoardTemplate:
    name = str(data.get("name") or fallback_name).strip()
    if not name:
        raise ValueError("Template name is required")
    cols_raw = data.get("columns")
    if not isinstance(cols_raw, list) or not cols_raw:
        raise ValueError(f"Template '{name}' needs a non-empty columns list")
    columns = tuple(
        _parse_column(c, i) for i, c in enumerate(cols_raw) if isinstance(c, dict)
    )
    if not columns:
        raise ValueError(f"Template '{name}' has no valid columns")

    def _str_tuple(key: str) -> tuple[str, ...]:
        val = data.get(key) or ()
        if isinstance(val, str):
            return (val,)
        return tuple(str(x) for x in val)

    return BoardTemplate(
        name=name,
        columns=columns,
        max_attempts=int(data.get("max_attempts", 3)),
        worker_capabilities=_str_tuple("worker_capabilities"),
        acceptance_criteria_defaults=_str_tuple("acceptance_criteria_defaults"),
        review_checklist=_str_tuple("review_checklist"),
    )


def load_workspace_templates() -> dict[str, BoardTemplate]:
    """Load optional workspace JSON templates (override built-ins by name)."""
    out: dict[str, BoardTemplate] = {}
    root = workspace_templates_dir()
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read template file %s: %s", path, exc)
            continue
        if not isinstance(raw, dict):
            logger.warning("Ignoring non-object template file %s", path)
            continue
        try:
            tmpl = _template_from_mapping(raw, fallback_name=path.stem)
        except ValueError as exc:
            logger.warning("Invalid workspace template %s: %s", path, exc)
            continue
        out[tmpl.name] = tmpl
    return out


def list_templates() -> dict[str, BoardTemplate]:
    """Built-in registry merged with workspace JSON (workspace wins on name clash)."""
    merged = dict(BUILTIN_TEMPLATES)
    merged.update(load_workspace_templates())
    return merged


def get_template(name: str) -> BoardTemplate:
    """Return a named board template or raise ``KeyError``."""
    registry = list_templates()
    try:
        return registry[name]
    except KeyError as exc:
        known = ", ".join(sorted(registry))
        raise KeyError(f"Unknown board template '{name}'. Known: {known}") from exc


def template_to_dict(template: BoardTemplate) -> dict[str, Any]:
    """JSON-serializable template summary (CLI / API helpers)."""
    data = asdict(template)
    data["columns"] = [asdict(c) for c in template.columns]
    data["worker_capabilities"] = list(template.worker_capabilities)
    data["acceptance_criteria_defaults"] = list(template.acceptance_criteria_defaults)
    data["review_checklist"] = list(template.review_checklist)
    return data


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


def claim_fields_for_template(template: BoardTemplate) -> dict[str, list[str]]:
    """Fields included on ticket claim responses for workers / gatekeepers."""
    return {
        "review_checklist": list(template.review_checklist),
        "acceptance_criteria_defaults": list(template.acceptance_criteria_defaults),
    }
