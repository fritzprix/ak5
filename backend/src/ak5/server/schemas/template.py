"""Board template API schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TemplateColumnOut(BaseModel):
    name: str
    stage: str
    position: int
    wip_limit: int = 0


class TemplateOut(BaseModel):
    name: str
    source: Literal["builtin", "workspace"] = "builtin"
    max_attempts: int = 3
    worker_capabilities: list[str] = Field(default_factory=list)
    acceptance_criteria_defaults: list[str] = Field(default_factory=list)
    review_checklist: list[str] = Field(default_factory=list)
    columns: list[TemplateColumnOut] = Field(default_factory=list)


class TemplateSummaryOut(BaseModel):
    """List row — same fields as TemplateOut (full shape for CLI adapter)."""

    name: str
    source: Literal["builtin", "workspace"] = "builtin"
    max_attempts: int = 3
    worker_capabilities: list[str] = Field(default_factory=list)
    acceptance_criteria_defaults: list[str] = Field(default_factory=list)
    review_checklist: list[str] = Field(default_factory=list)
    columns: list[TemplateColumnOut] = Field(default_factory=list)
