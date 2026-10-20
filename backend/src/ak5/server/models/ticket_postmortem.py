"""Ticket postmortem records (workflow.md §9)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base


class TicketPostmortem(Base):
    __tablename__ = "ticket_postmortems"

    postmortem_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    ticket_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tickets.ticket_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    what_went_wrong: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    revision_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ticket_revisions.revision_id", ondelete="SET NULL"),
        nullable=True,
    )
    human_review_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ticket_reviews.review_id", ondelete="SET NULL"),
        nullable=True,
    )
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )
