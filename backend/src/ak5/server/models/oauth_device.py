"""AK5-as-AS device authorization codes (identity.md §3.3 / §4.1, RFC 8628)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base


class OAuthDeviceCode(Base):
    """Pending/approved device grant distinct from legacy ``device_codes`` (actor flow)."""

    __tablename__ = "oauth_device_codes"

    device_code: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    principal: Mapped[str] = mapped_column(String(16), nullable=False)  # user|agent
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )  # pending|approved|denied|expired|consumed
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Agent registration request fields (unused for principal=user).
    requested_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_code_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )
