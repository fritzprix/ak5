"""Pydantic schemas for workspace / membership / invite APIs (#34)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)


class WorkspacePolicyPatch(BaseModel):
    require_user_presence_for_approval: bool | None = None
    allowed_idp_issuers: list[str] | None = None
    allowed_email_domains: list[str] | None = None
    max_auto_boards: int | None = Field(default=None, ge=0)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    policy: WorkspacePolicyPatch | None = None


class WorkspaceOut(BaseModel):
    workspace_id: str
    name: str
    policy: dict[str, Any]
    inbox_board_id: str | None = None
    membership_role: str | None = None


class MemberOut(BaseModel):
    user_id: str
    email: str | None
    display_name: str
    role: str
    membership_id: str


class MemberRoleUpdate(BaseModel):
    role: Literal["owner", "admin", "member"]


class InviteCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    role: Literal["admin", "member"] = "member"


class InviteOut(BaseModel):
    invite_id: str
    workspace_id: str
    email: str
    role: str
    expires_at: datetime
    # Plaintext token returned only on create.
    token: str | None = None


class InviteAccept(BaseModel):
    token: str = Field(..., min_length=8)


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    workflow_role: Literal["triage", "worker", "gatekeeper"] = "worker"
    capabilities: list[str] = Field(default_factory=list)
    board_scope: list[str] = Field(default_factory=list)


class AgentCredentialIssue(BaseModel):
    board_scope: list[str] | None = None


class AgentCredentialOut(BaseModel):
    credential_id: str
    board_scope: list[str]
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    # Plaintext only on create/issue responses.
    access_token: str | None = None


class AgentOut(BaseModel):
    agent_id: str
    workspace_id: str
    name: str
    workflow_role: str
    capabilities: list[str]
    board_scope: list[str]
    created_by: str
    disabled_at: datetime | None = None
    credentials: list[AgentCredentialOut] = Field(default_factory=list)


class SessionTokenRequest(BaseModel):
    workspace_id: str | None = None


class SessionTokenOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    workspace_id: str
    user_id: str
    membership_role: str | None = None
