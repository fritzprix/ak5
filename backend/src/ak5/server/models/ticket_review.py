"""Ticket review / verdict records (workflow.md §9)."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID


class TicketReview(Base):
    __tablename__ = "ticket_reviews"

    review_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default=LOCAL_WORKSPACE_ID, index=True
    )
    ticket_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tickets.ticket_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # approve | reject
    findings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    misrouted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    @property
    def findings(self) -> list[dict[str, Any]]:
        import json

        try:
            parsed = json.loads(self.findings_json)
        except (json.JSONDecodeError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []

    @findings.setter
    def findings(self, val: list[dict[str, Any]]) -> None:
        import json

        self.findings_json = json.dumps(val)
