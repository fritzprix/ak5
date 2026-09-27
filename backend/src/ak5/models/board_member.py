from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ak5.models.base import Base

if TYPE_CHECKING:
    from ak5.models.actor import Actor
    from ak5.models.board import Board


class BoardMember(Base):
    """Associates an Actor (Human or Agent) with a Board."""

    __tablename__ = "board_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("boards.board_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("actors.actor_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True, default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    board: Mapped["Board"] = relationship("Board", back_populates="members")
    actor: Mapped["Actor"] = relationship("Actor")

    __table_args__ = (
        UniqueConstraint("board_id", "actor_id", name="uq_board_actor"),
    )
