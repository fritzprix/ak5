"""Structured workflow payloads with validation (workflow.md §5)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class RouteTargetBoard(BaseModel):
    board_id: str = Field(..., min_length=1)


class RouteCreateBoard(BaseModel):
    name: str = Field(..., min_length=1)
    template: str = Field(default="default", min_length=1)
    description: str = ""


class RouteTargetCreate(BaseModel):
    create_board: RouteCreateBoard


class RoutePlan(BaseModel):
    """Route payload (workflow.md §5.1)."""

    refined_title: str = Field(..., min_length=1)
    refined_spec: str = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    target: RouteTargetBoard | RouteTargetCreate
    confidence: float = Field(..., ge=0.0, le=1.0)


class Artifact(BaseModel):
    kind: Literal["url", "attachment", "commit", "text"]
    ref: str = Field(..., min_length=1)


class CriteriaSelfCheck(BaseModel):
    criterion: str = Field(..., min_length=1)
    pass_: bool = Field(..., alias="pass")
    note: str = ""

    model_config = {"populate_by_name": True}


class Deliverable(BaseModel):
    """Worker submit deliverable (workflow.md §5.2)."""

    summary: str = Field(..., min_length=1)
    artifacts: list[Artifact] = Field(default_factory=list)
    criteria_self_check: list[CriteriaSelfCheck] = Field(default_factory=list)


class Finding(BaseModel):
    criterion: str = Field(..., min_length=1)
    pass_: bool = Field(..., alias="pass")
    note: str = ""

    model_config = {"populate_by_name": True}


class Verdict(BaseModel):
    """Gatekeeper / human verdict (workflow.md §5.3).

    Validation:
    - ``approve`` → every finding must pass
    - ``reject`` (not misrouted) → at least one fail finding
    """

    decision: Literal["approve", "reject"]
    reason: str = ""
    misrouted: bool = False
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_findings(self) -> Verdict:
        if self.decision == "approve":
            if self.misrouted:
                raise ValueError("approve verdict cannot set misrouted=true")
            if any(not f.pass_ for f in self.findings):
                raise ValueError("approve verdict requires all findings to pass")
            return self

        # reject
        if self.misrouted:
            return self
        if not any(not f.pass_ for f in self.findings):
            raise ValueError("reject verdict requires at least one fail finding")
        return self


class Postmortem(BaseModel):
    """Postmortem body after human reject / reopen (workflow.md §5.4)."""

    what_went_wrong: str = Field(..., min_length=1)
    root_cause: str = Field(..., min_length=1)
    revised_spec: str = Field(..., min_length=1)
    revised_acceptance_criteria: Annotated[list[str], Field(min_length=1)]
