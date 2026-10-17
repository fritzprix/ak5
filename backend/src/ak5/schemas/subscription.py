from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SubscriptionBase(BaseModel):
    board_id: str | None = Field(default=None, max_length=128, description="Target board ID or None for all boards")
    # Nullable in DB for v2 daemon hooks (#11 removes server exec); create still requires it.
    exec_command: str | None = Field(
        default=None,
        max_length=2048,
        description="Literal shell hook command (event via env/stdin); optional for v2 daemon",
    )
    events: list[str] | str | None = Field(default=None, description="Event types to match")
    for_agent: str | None = Field(default=None, max_length=128, description="Filter by assigned agent ID")
    ignore_actor: str | None = Field(default=None, max_length=128, description="Ignore events triggered by this actor")
    debounce_seconds: float = Field(default=0.0, ge=0.0, le=3600.0, description="Debounce cooldown in seconds")


class SubscriptionCreate(SubscriptionBase):
    subscription_id: str | None = Field(default=None, max_length=128, description="Optional custom ID")
    exec_command: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Literal shell hook command (event via env/stdin)",
    )


class SubscriptionOut(SubscriptionBase):
    subscription_id: str
    created_by: str
    created_at: datetime
    ack_cursor: int = 0
    consumer_id: str | None = None
    consumer_lease_expires_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
