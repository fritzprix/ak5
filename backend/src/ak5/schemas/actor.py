from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ActorBase(BaseModel):
    actor_id: str = Field(..., description="Unique actor ID (e.g. user_david, agent_image_worker)")
    actor_type: Literal["human", "agent"] = Field(..., description="human or agent")
    name: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    status: Literal["idle", "busy", "offline"] = "idle"
    avatar_url: str | None = None


class ActorIdentifyRequest(BaseModel):
    actor_id: str
    actor_type: Literal["human", "agent"] = "agent"
    name: str
    role: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    avatar_url: str | None = None


class ActorUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    capabilities: list[str] | None = None
    status: Literal["idle", "busy", "offline"] | None = None
    avatar_url: str | None = None


class ActorOut(ActorBase):
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    actor: ActorOut
