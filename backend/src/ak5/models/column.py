from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ak5.models.base import Base

if TYPE_CHECKING:
    from ak5.models.board import Board
    from ak5.models.ticket import Ticket


class Column(Base):
    __tablename__ = "columns"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('open', 'in_progress', 'review', 'done')",
            name="check_column_stage",
        ),
    )

    column_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    board_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("boards.board_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="open")  # open, in_progress, review, done
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    wip_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    board: Mapped["Board"] = relationship("Board", back_populates="columns")
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        back_populates="column",
        cascade="all, delete-orphan",
        order_by="Ticket.rank",
    )
