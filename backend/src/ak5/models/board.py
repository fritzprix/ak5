from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ak5.models.base import Base

if TYPE_CHECKING:
    from ak5.models.actor import Actor
    from ak5.models.column import Column
    from ak5.models.ticket import Ticket


class Board(Base):
    __tablename__ = "boards"

    board_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), ForeignKey("actors.actor_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    creator: Mapped["Actor"] = relationship("Actor", foreign_keys=[created_by])
    columns: Mapped[list["Column"]] = relationship(
        "Column",
        back_populates="board",
        cascade="all, delete-orphan",
        order_by="Column.position",
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        back_populates="board",
        cascade="all, delete-orphan",
    )
