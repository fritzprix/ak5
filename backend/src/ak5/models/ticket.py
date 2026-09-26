import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ak5.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from ak5.models.actor import Actor
    from ak5.models.board import Board
    from ak5.models.column import Column


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')",
            name="check_ticket_priority",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'blocked', 'done')",
            name="check_ticket_status",
        ),
        Index("idx_tickets_board", "board_id"),
        Index("idx_tickets_column_rank", "column_id", "rank"),
        Index("idx_tickets_assigned", "assigned_to"),
        Index("idx_tickets_parent", "parent_ticket_id"),
    )

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    board_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("boards.board_id", ondelete="CASCADE"),
        nullable=False,
    )
    column_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("columns.column_id"),
        nullable=False,
    )
    parent_ticket_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("tickets.ticket_id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    rank: Mapped[str] = mapped_column(String(128), nullable=False)  # Lexorank string
    labels: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON array
    assigned_to: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("actors.actor_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("actors.actor_id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # Free-text block reason (not a ticket FK). Agents/PMs record why work is blocked.
    blocked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON object
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    board: Mapped["Board"] = relationship("Board", back_populates="tickets")
    column: Mapped["Column"] = relationship("Column", back_populates="tickets")
    creator: Mapped["Actor"] = relationship("Actor", foreign_keys=[created_by])
    assignee: Mapped["Actor | None"] = relationship("Actor", foreign_keys=[assigned_to])

    parent: Mapped["Ticket | None"] = relationship(
        "Ticket",
        remote_side=[ticket_id],
        foreign_keys=[parent_ticket_id],
        back_populates="subtasks",
    )
    subtasks: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        foreign_keys=[parent_ticket_id],
        back_populates="parent",
    )

    comments: Mapped[list["TicketComment"]] = relationship(
        "TicketComment",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketComment.created_at",
    )

    @property
    def label_list(self) -> list[str]:
        try:
            return json.loads(self.labels)
        except (json.JSONDecodeError, TypeError):
            return []

    @label_list.setter
    def label_list(self, val: list[str]) -> None:
        self.labels = json.dumps(val)

    @property
    def execution_context_dict(self) -> dict[str, Any] | None:
        if not self.execution_context:
            return None
        try:
            return json.loads(self.execution_context)
        except (json.JSONDecodeError, TypeError):
            return {}

    @execution_context_dict.setter
    def execution_context_dict(self, val: dict[str, Any] | None) -> None:
        self.execution_context = json.dumps(val) if val is not None else None


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    comment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tickets.ticket_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("actors.actor_id"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="comments")
    author: Mapped["Actor"] = relationship("Actor", foreign_keys=[actor_id])
