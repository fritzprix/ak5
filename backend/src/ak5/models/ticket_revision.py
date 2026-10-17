"""Ticket revision history (workflow.md §9)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.models.base import Base


class TicketRevision(Base):
    __tablename__ = "ticket_revisions"

    revision_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tickets.ticket_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    spec: Mapped[str] = mapped_column(Text, nullable=False, default="")
    acceptance_criteria_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="direct",
    )  # route | postmortem | unblock | direct
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    @property
    def acceptance_criteria(self) -> list[str]:
        import json

        try:
            parsed = json.loads(self.acceptance_criteria_json)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]

    @acceptance_criteria.setter
    def acceptance_criteria(self, val: list[str]) -> None:
        import json

        self.acceptance_criteria_json = json.dumps(val)
