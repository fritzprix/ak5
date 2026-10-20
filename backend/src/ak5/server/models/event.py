"""Persistent event log (events.md §1)."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("idx_events_board_seq", "board_id", "seq"),
        Index("idx_events_type", "type"),
        Index("idx_events_created", "created_at"),
        Index("idx_events_workspace_seq", "workspace_id", "seq"),
        # Without AUTOINCREMENT, SQLite may reuse rowids after a full purge,
        # causing cursors to silently skip events (events.md §1).
        {"sqlite_autoincrement": True},
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    board_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    @property
    def data(self) -> dict[str, Any]:
        import json

        try:
            parsed = json.loads(self.data_json)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
