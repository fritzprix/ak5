"""Idempotency key store for write operations (workflow.md §7)."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (Index("idx_idempotency_actor_op", "actor", "op"),)

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    op: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )
