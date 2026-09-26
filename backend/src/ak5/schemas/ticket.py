from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class TicketCreate(TicketBase):
    board_id: str
    column_id: str
    ticket_id: str | None = None  # optional client-supplied or auto-generated
    parent_ticket_id: str | None = None
    assigned_to: str | None = None
    execution_context: dict[str, Any] | None = None


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


class TicketMoveRequest(BaseModel):
    target_column_id: str
    previous_ticket_id: str | None = None
    next_ticket_id: str | None = None


class TicketDelegateRequest(BaseModel):
    target_actor_id: str
    subtask_title: str = Field(..., min_length=1, max_length=256)
    subtask_description: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    labels: list[str] = Field(default_factory=list)
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
    created_at: datetime
    updated_at: datetime

    # Subtasks count or summaries
    subtask_count: int = 0
    subtask_done_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class TicketDetailOut(TicketOut):
    comments: list[TicketCommentOut] = Field(default_factory=list)
    subtasks: list[TicketOut] = Field(default_factory=list)
