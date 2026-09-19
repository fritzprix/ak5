from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    log_id: int
    actor_id: str
    action: str
    target_type: str
    target_id: str
    payload: dict[str, Any] | None = None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)
