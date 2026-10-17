"""Error codes, HTTP/exit mappings, and error envelope (workflow.md §7, events.md §5, cli.md §3.1)."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """Stable machine-readable error codes."""

    # workflow.md §7
    INVALID_TRANSITION = "INVALID_TRANSITION"
    ROLE_FORBIDDEN = "ROLE_FORBIDDEN"
    SEPARATION_OF_DUTIES = "SEPARATION_OF_DUTIES"
    CLAIM_CONFLICT = "CLAIM_CONFLICT"
    STALE_CLAIM = "STALE_CLAIM"
    TICKET_BLOCKED = "TICKET_BLOCKED"
    WIP_LIMIT = "WIP_LIMIT"
    PAYLOAD_INVALID = "PAYLOAD_INVALID"
    SYSTEM_BOARD = "SYSTEM_BOARD"

    # events.md §5
    CURSOR_EXPIRED = "CURSOR_EXPIRED"
    CONSUMER_CONFLICT = "CONSUMER_CONFLICT"
    SUBSCRIPTION_NOT_FOUND = "SUBSCRIPTION_NOT_FOUND"

    # cli.md §2 / §3.1
    VERSION_MISMATCH = "VERSION_MISMATCH"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_TRANSITION: 409,
    ErrorCode.ROLE_FORBIDDEN: 403,
    ErrorCode.SEPARATION_OF_DUTIES: 403,
    ErrorCode.CLAIM_CONFLICT: 409,
    ErrorCode.STALE_CLAIM: 409,
    ErrorCode.TICKET_BLOCKED: 423,
    ErrorCode.WIP_LIMIT: 409,
    ErrorCode.PAYLOAD_INVALID: 422,
    ErrorCode.SYSTEM_BOARD: 403,
    ErrorCode.CURSOR_EXPIRED: 410,
    ErrorCode.CONSUMER_CONFLICT: 409,
    ErrorCode.SUBSCRIPTION_NOT_FOUND: 404,
    ErrorCode.VERSION_MISMATCH: 409,
}

EXIT_CODE: dict[ErrorCode, int] = {
    ErrorCode.PAYLOAD_INVALID: 2,
    ErrorCode.CLAIM_CONFLICT: 3,
    ErrorCode.STALE_CLAIM: 3,
    ErrorCode.CONSUMER_CONFLICT: 3,
    ErrorCode.INVALID_TRANSITION: 4,
    ErrorCode.ROLE_FORBIDDEN: 4,
    ErrorCode.SEPARATION_OF_DUTIES: 4,
    ErrorCode.SYSTEM_BOARD: 4,
    ErrorCode.WIP_LIMIT: 4,
    ErrorCode.TICKET_BLOCKED: 5,
    ErrorCode.VERSION_MISMATCH: 7,
    # events.md codes without an explicit cli.md exit → treat as generic failure
    ErrorCode.CURSOR_EXPIRED: 1,
    ErrorCode.SUBSCRIPTION_NOT_FOUND: 1,
}


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """API / CLI error body: ``{"error": {"code", "message", "details"}}``."""

    error: ErrorDetail


def error_body(
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a plain dict matching :class:`ErrorEnvelope` for JSON responses."""
    return ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details or {}),
    ).model_dump(mode="json")
