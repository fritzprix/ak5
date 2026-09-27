from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.models.base import Base


class DeviceCode(Base):
    """Device authorization session for OAuth 2.0 Device Code Flow (RFC 8628)."""

    __tablename__ = "device_codes"

    device_code: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), default="agent", nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    capabilities: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)  # pending, approved, denied, expired
    target_board_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of board_ids
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )
