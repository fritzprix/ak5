from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SubscriptionFilter(BaseModel):
    """Server-side filter only (events.md §2). Hooks live on the client daemon."""

    board_id: str | None = Field(default=None, max_length=128)
    events: list[str] | None = Field(default=None)
    role: str | None = Field(default=None, max_length=64)
    capabilities: list[str] | None = Field(default=None)
    for_actor: str | None = Field(default=None, max_length=128)


class SubscriptionConsumer(BaseModel):
    consumer_id: str
    lease_expires_at: datetime


class SubscriptionCreate(BaseModel):
    subscription_id: str | None = Field(default=None, max_length=128)
    filter: SubscriptionFilter = Field(default_factory=SubscriptionFilter)
    # "now" = start at current max(seq) (skip retention history); "earliest" = replay.
    start: Literal["now", "earliest"] = "now"


class SubscriptionAckIn(BaseModel):
    cursor: int = Field(..., ge=0)


class SubscriptionAckOut(BaseModel):
    ack_cursor: int


class LongPollEventsOut(BaseModel):
    events: list[dict]
    next_cursor: int


class SubscriptionOut(BaseModel):
    subscription_id: str
    owner: str
    filter: SubscriptionFilter
    ack_cursor: int = 0
    consumer: SubscriptionConsumer | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
