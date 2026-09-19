from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from ak5.schemas.column import ColumnOut
from ak5.schemas.ticket import TicketOut


class BoardBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None


class BoardCreate(BoardBase):
    board_id: str | None = None  # Auto-generated if not supplied


class BoardOut(BoardBase):
    board_id: str
    created_by: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ColumnWithTicketsOut(ColumnOut):
    tickets: list[TicketOut] = Field(default_factory=list)


class BoardDetailOut(BoardOut):
    columns: list[ColumnWithTicketsOut] = Field(default_factory=list)
