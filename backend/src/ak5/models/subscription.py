from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.models.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    board_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Filter fields (events.md §2) — no server-side exec_command.
    events: Mapped[str | None] = mapped_column(Text, nullable=True)  # Comma-separated
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    # API name: for_actor (legacy column name retained for migration simplicity)
    for_agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )
    # events.md §2 — server stores filter + ack cursor only
    ack_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    consumer_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
