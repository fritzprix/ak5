"""Server re-export — prefer ``ak5.services.templates`` (client-safe registry)."""

from __future__ import annotations

from ak5.services.templates import (  # noqa: F401
    BUILTIN_TEMPLATES,
    CODING_TEMPLATE,
    DEFAULT_TEMPLATE,
    INBOX_TEMPLATE,
    RESEARCH_TEMPLATE,
    TEMPLATES,
    BoardTemplate,
    ColumnSpec,
    claim_fields_for_template,
    get_template,
    list_templates,
    load_workspace_templates,
    template_column_dicts,
    template_to_dict,
    workspace_templates_dir,
)

__all__ = [
    "BUILTIN_TEMPLATES",
    "CODING_TEMPLATE",
    "DEFAULT_TEMPLATE",
    "INBOX_TEMPLATE",
    "RESEARCH_TEMPLATE",
    "TEMPLATES",
    "BoardTemplate",
    "ColumnSpec",
    "claim_fields_for_template",
    "get_template",
    "list_templates",
    "load_workspace_templates",
    "template_column_dicts",
    "template_to_dict",
    "workspace_templates_dir",
]
