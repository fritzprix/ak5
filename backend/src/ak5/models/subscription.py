from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.models.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    board_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    exec_command: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[str | None] = mapped_column(Text, nullable=True)  # Comma-separated or JSON
    for_agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ignore_actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    debounce_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )
