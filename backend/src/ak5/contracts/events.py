"""Event types and WORK_AVAILABLE payload (events.md §1.1)."""

from enum import StrEnum

from pydantic import BaseModel, Field

from ak5.contracts.stages import Role, Stage


class EventType(StrEnum):
    """Persisted event log types (events.md §1.1)."""

    WORK_AVAILABLE = "WORK_AVAILABLE"
    TICKET_TRANSITIONED = "TICKET_TRANSITIONED"
    TICKET_BLOCKED = "TICKET_BLOCKED"
    TICKET_UNBLOCKED = "TICKET_UNBLOCKED"
    HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"
    # Legacy / compatibility
    TICKET_CREATED = "TICKET_CREATED"
    TICKET_UPDATED = "TICKET_UPDATED"
    COMMENT_ADDED = "COMMENT_ADDED"


class WorkAvailableData(BaseModel):
    """``WORK_AVAILABLE.data`` shape (events.md §1.1)."""

    board_id: str
    stage: Stage | str
    required_role: Role
    capabilities: list[str] = Field(default_factory=list)
