from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SubscriptionBase(BaseModel):
    board_id: str | None = Field(default=None, max_length=128, description="Target board ID or None for all boards")
    exec_command: str = Field(..., min_length=1, max_length=2048, description="Literal shell hook command (event via env/stdin)")
    events: list[str] | str | None = Field(default=None, description="Event types to match")
    for_agent: str | None = Field(default=None, max_length=128, description="Filter by assigned agent ID")
    ignore_actor: str | None = Field(default=None, max_length=128, description="Ignore events triggered by this actor")
    debounce_seconds: float = Field(default=0.0, ge=0.0, le=3600.0, description="Debounce cooldown in seconds")


class SubscriptionCreate(SubscriptionBase):
    subscription_id: str | None = Field(default=None, max_length=128, description="Optional custom ID")


class SubscriptionOut(SubscriptionBase):
    subscription_id: str
    created_by: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
