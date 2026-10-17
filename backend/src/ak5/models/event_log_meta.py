"""Persisted event-log housekeeping (retention purge watermark)."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ak5.models.base import Base


class EventLogMeta(Base):
    """Small key/value table for event-log meta (e.g. purged_through_seq)."""

    __tablename__ = "event_log_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
