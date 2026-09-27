from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ak5.schemas.actor import ActorOut


class DeviceCodeRequest(BaseModel):
    actor_id: str = Field(..., description="Unique actor ID (e.g. agent-code-reviewer)")
    actor_type: str = Field(default="agent", description="'agent' or 'human'")
    name: str | None = Field(None, description="Display name")
    role: str = Field(..., description="Role of the actor")
    capabilities: list[str] = Field(default_factory=list, description="Actor capabilities")
    board_id: str | None = Field(None, description="Optional target board ID to request membership for")


class DeviceCodeResponse(BaseModel):
    device_code: str = Field(..., description="Secret device verification code used for polling")
    user_code: str = Field(..., description="User-facing verification code (e.g. AK5-7892)")
    verification_uri: str = Field(..., description="URL where the user should enter the user code")
    verification_uri_complete: str = Field(..., description="URL pre-filled with the user code for 1-click approval")
    expires_in: int = Field(..., description="Lifetime of code in seconds")
    interval: int = Field(default=3, description="Minimum polling interval in seconds")


class DeviceTokenRequest(BaseModel):
    device_code: str = Field(..., description="Device code returned in initial handshake")


class DeviceVerificationView(BaseModel):
    user_code: str
    actor_id: str
    actor_type: str
    name: str
    role: str
    capabilities: list[str]
    status: str
    target_board_ids: list[str] = Field(default_factory=list)
    expires_at: datetime


class DeviceApprovalRequest(BaseModel):
    user_code: str = Field(..., description="User code to approve or deny")
    approved: bool = Field(..., description="True to approve, False to deny")
    board_ids: list[str] = Field(default_factory=list, description="Board IDs to enroll the actor in")


class BoardMemberCreate(BaseModel):
    actor_id: str = Field(..., description="Actor ID to add as a board member")
    role: str | None = Field(default="member", description="Role within the board")


class BoardMemberOut(BaseModel):
    id: int
    board_id: str
    actor_id: str
    role: str | None
    created_at: datetime
    actor: ActorOut | None = None
