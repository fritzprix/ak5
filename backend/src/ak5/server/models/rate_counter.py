"""Fixed-window request counters shared by every instance (operations.md §2.2, #114)."""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base


class RateCounter(Base):
    """One row per ``(key, window)``; ``key`` is ``<rule>:<subject>`` (e.g. ``write_principal:usr_1``)."""

    __tablename__ = "rate_counters"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, primary_key=True)  # epoch seconds
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
