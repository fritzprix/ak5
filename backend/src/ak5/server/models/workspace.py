"""Workspace tenancy root (identity.md §1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base, TimestampMixin

LOCAL_WORKSPACE_ID = "ws_local"


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    policy: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # usr_…
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)


class UserIdentity(Base):
    __tablename__ = "user_identities"

    identity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now(), nullable=False
    )


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"

    membership_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # owner|admin|member


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # agt_…
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    workflow_role: Mapped[str] = mapped_column(String(32), nullable=False)
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    board_scope: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def capability_list(self) -> list[str]:
        import json

        try:
            return json.loads(self.capabilities)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def board_scope_list(self) -> list[str]:
        import json

        try:
            return json.loads(self.board_scope)
        except (json.JSONDecodeError, TypeError):
            return []


class AgentCredential(Base, TimestampMixin):
    __tablename__ = "agent_credentials"

    credential_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    board_scope: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    client: Mapped[str] = mapped_column(String(16), nullable=False)  # web|cli
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkspaceUsage(Base):
    __tablename__ = "workspace_usage"

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), server_default=func.now(), nullable=False
    )

