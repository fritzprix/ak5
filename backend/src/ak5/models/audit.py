import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(128), ForeignKey("actors.actor_id"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # CREATED, MOVED, DELEGATED, STATUS_CHANGE
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # ticket, board, column
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    @property
    def payload_dict(self) -> dict[str, Any] | None:
        if not self.payload:
            return None
        try:
            return json.loads(self.payload)
        except (json.JSONDecodeError, TypeError):
            return {}

    @payload_dict.setter
    def payload_dict(self, val: dict[str, Any] | None) -> None:
        self.payload = json.dumps(val) if val is not None else None
