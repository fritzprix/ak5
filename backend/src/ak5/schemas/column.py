from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ColumnBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    stage: Literal["open", "in_progress", "review", "done"] = "open"
    position: int = Field(default=1, ge=1)
    wip_limit: int = Field(default=0, ge=0)


class ColumnCreate(ColumnBase):
    column_id: str = Field(..., min_length=1, max_length=128)


class ColumnOut(ColumnBase):
    column_id: str
    board_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
