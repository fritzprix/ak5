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
