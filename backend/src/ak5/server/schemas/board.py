from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ak5.server.schemas.column import ColumnOut
from ak5.server.schemas.ticket import SubtaskSummaryOut, TicketOut


class BoardTicketOut(TicketOut):
    """Board column ticket with one-query subtask summaries (may be cross-board)."""

    subtasks: list[SubtaskSummaryOut] = Field(default_factory=list)


class BoardBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None


class BoardCreate(BoardBase):
    board_id: str | None = None  # Auto-generated if not supplied
    template: str = Field(
        default="default",
        description="Board template name (defines columns / max_attempts)",
    )
    owner_actor_ids: list[str] = Field(
        default_factory=list,
        description="Human actors to enroll as board admins at creation",
    )


class BoardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    template: str | None = Field(
        default=None,
        description="Switch board template; missing stage columns are added (never deleted)",
    )


class BoardOut(BoardBase):
    board_id: str
    created_by: str
    created_at: datetime
    kind: Literal["inbox", "standard"] = "standard"
    template: str | None = None
    auto_created: bool = False

    model_config = ConfigDict(from_attributes=True)


class BoardClaimOut(BaseModel):
    board_id: str
    actor_id: str
    role: str
    message: str


class ColumnWithTicketsOut(ColumnOut):
    tickets: list[BoardTicketOut] = Field(default_factory=list)
    total_ticket_count: int | None = None


class BoardDetailOut(BoardOut):
    columns: list[ColumnWithTicketsOut] = Field(default_factory=list)
    has_human_admin: bool = Field(
        default=False,
        description="True if a human already has board admin (creator or BoardMember)",
    )
