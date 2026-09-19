import json
from typing import Any
from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ak5.models.base import Base, TimestampMixin


class Actor(Base, TimestampMixin):
    __tablename__ = "actors"
    __table_args__ = (
        CheckConstraint("actor_type IN ('human', 'agent')", name="check_actor_type"),
        CheckConstraint("status IN ('idle', 'busy', 'offline')", name="check_actor_status"),
    )

    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'human', 'agent'
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON string
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")  # idle, busy, offline
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Helper property for JSON capabilities
    @property
    def capability_list(self) -> list[str]:
        try:
            return json.loads(self.capabilities)
        except Exception:
            return []

    @capability_list.setter
    def capability_list(self, val: list[str]) -> None:
        self.capabilities = json.dumps(val)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "capabilities": self.capability_list,
            "status": self.status,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
