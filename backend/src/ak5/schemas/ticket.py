from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ak5.contracts.payloads import Deliverable, Postmortem, RoutePlan
from ak5.contracts.stages import BlockedKind


class TicketCommentBase(BaseModel):
    content: str = Field(..., min_length=1)
    is_internal: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TicketCommentCreate(TicketCommentBase):
    pass


class TicketCommentOut(TicketCommentBase):
    comment_id: str
    ticket_id: str
    actor_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    labels: list[str] = Field(default_factory=list)
    due_date: datetime | None = None


class TicketCreate(BaseModel):
    """Create ticket — T1 (no board) or T2 (board + spec/criteria).

    Legacy ``column_id`` is accepted but ignored; T2 always lands on backlog.
    """

    title: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    labels: list[str] = Field(default_factory=list)
    due_date: datetime | None = None
    board_id: str | None = None
    column_id: str | None = None  # ignored (compat with v1 clients)
    ticket_id: str | None = None
    parent_ticket_id: str | None = None
    assigned_to: str | None = None
    execution_context: dict[str, Any] | None = None
    # T1
    original_request: str | None = None
    # T2
    spec: str | None = None
    acceptance_criteria: list[str] | None = None
    max_attempts: int | None = Field(default=None, ge=1)


class TicketUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    labels: list[str] | None = None
    assigned_to: str | None = None
    status: Literal["open", "in_progress", "blocked", "done"] | None = None
    blocked_by: str | None = Field(default=None, max_length=2048)
    execution_context: dict[str, Any] | None = None
    due_date: datetime | None = None
    is_archived: bool | None = None


class TicketMoveRequest(BaseModel):
    target_column_id: str
    previous_ticket_id: str | None = None
    next_ticket_id: str | None = None
    # Required for stage changes that mirror T16/T17 (human reject / reopen).
    reason: str | None = None


class TicketDelegateRequest(BaseModel):
    """Delegate as create_direct with parent (cli.md §5) — pull model, no assignee."""

    subtask_title: str = Field(..., min_length=1, max_length=256)
    subtask_description: str | None = None
    spec: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    labels: list[str] = Field(default_factory=list)
    board_id: str | None = None  # default: parent's board
    # Legacy field kept optional so old clients don't 422; ignored.
    target_actor_id: str | None = None
    execution_context: dict[str, Any] | None = None


class TicketOut(TicketBase):
    ticket_id: str
    board_id: str
    column_id: str
    parent_ticket_id: str | None = None
    rank: str
    assigned_to: str | None = None
    created_by: str
    status: Literal["open", "in_progress", "blocked", "done"]
    blocked_by: str | None = None
    execution_context: dict[str, Any] | None = None
    is_archived: bool = False
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Workflow overlays (workflow.md §2 / §9)
    stage: str | None = None
    attempt: int = 0
    max_attempts: int = 3
    claim_token: str | None = None
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None
    lease_expirations: int = 0
    submitted_by: str | None = None
    blocked_kind: str | None = None
    blocked_since: datetime | None = None
    blocked_actor: str | None = None
    original_request: str | None = None

    # Subtasks count or summaries
    subtask_count: int = 0
    subtask_done_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class SubtaskSummaryOut(BaseModel):
    """Compact child ticket for board listing (workflow.md board accordion)."""

    ticket_id: str
    title: str
    stage: str | None = None
    board_id: str
    blocked: bool = False
    claimed: bool = False


class TicketAttachmentOut(BaseModel):
    attachment_id: str
    ticket_id: str
    actor_id: str
    filename: str
    file_size: int
    content_type: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketDetailOut(TicketOut):
    comments: list[TicketCommentOut] = Field(default_factory=list)
    subtasks: list[TicketOut] = Field(default_factory=list)
    attachments: list[TicketAttachmentOut] = Field(default_factory=list)


class TicketRevisionOut(BaseModel):
    revision_id: int
    ticket_id: str
    spec: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    author: str
    reason: str | None = None
    source: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketReviewOut(BaseModel):
    review_id: int
    ticket_id: str
    stage: str
    decision: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    reviewer: str
    attempt: int
    reason: str | None = None
    misrouted: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketPostmortemOut(BaseModel):
    postmortem_id: int
    ticket_id: str
    what_went_wrong: str
    root_cause: str
    revision_id: int | None = None
    human_review_id: int | None = None
    author: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogOut(BaseModel):
    log_id: int
    actor_id: str
    action: str
    target_type: str
    target_id: str
    payload: dict[str, Any] | None = None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketHistoryOut(BaseModel):
    ticket_id: str
    revisions: list[TicketRevisionOut] = Field(default_factory=list)
    reviews: list[TicketReviewOut] = Field(default_factory=list)
    postmortems: list[TicketPostmortemOut] = Field(default_factory=list)
    audit: list[AuditLogOut] = Field(default_factory=list)


class TicketClaimOut(BaseModel):
    ticket: TicketOut
    claim_token: str
    lease_expires_at: datetime
    revision: TicketRevisionOut | None = None
    postmortems: list[TicketPostmortemOut] = Field(default_factory=list)
    reviews: list[TicketReviewOut] = Field(default_factory=list)


class ClaimRequest(BaseModel):
    capabilities: list[str] | None = None


class HeartbeatRequest(BaseModel):
    claim_token: str = Field(..., min_length=1)


class ReleaseRequest(BaseModel):
    claim_token: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class SubmitRequest(BaseModel):
    claim_token: str = Field(..., min_length=1)
    deliverable: Deliverable


class BounceRequest(BaseModel):
    claim_token: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class RouteRequest(BaseModel):
    claim_token: str = Field(..., min_length=1)
    plan: RoutePlan


class VerdictRequestBody(BaseModel):
    """API verdict body — gatekeeper rules enforced in workflow service.

    Contracts ``Verdict`` requires fail findings on every reject; human reject
    (T16) only needs ``reason`` (orchestrator note on #10). Validation lives
    in ``services.workflow.verdict``.
    """

    decision: Literal["approve", "reject"]
    reason: str = ""
    misrouted: bool = False
    findings: list[dict[str, Any]] = Field(default_factory=list)


class VerdictRequest(BaseModel):
    claim_token: str | None = None
    verdict: VerdictRequestBody


class PostmortemRequest(BaseModel):
    claim_token: str = Field(..., min_length=1)
    postmortem: Postmortem


class BlockRequest(BaseModel):
    kind: BlockedKind
    reason: str = Field(..., min_length=1)
    claim_token: str | None = None


class UnblockRequest(BaseModel):
    resolution: str | None = None


class ReopenRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class RerouteRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    to_inbox: bool = False
    target_board_id: str | None = None
