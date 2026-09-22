from typing import Any

from pydantic import BaseModel


class SSEEventOut(BaseModel):
    event_id: int
    event_type: str
    data: dict[str, Any]
    timestamp: str
