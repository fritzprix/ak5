"""Client-side errors and exit-code mapping (cli.md §3.1)."""

from __future__ import annotations

from typing import Any

from ak5.contracts.errors import EXIT_CODE, ErrorCode, error_body

# Exit codes without a matching ErrorCode (cli.md §3.1)
EXIT_SUCCESS = 0
EXIT_GENERIC = 1
EXIT_USAGE = 2
EXIT_EMPTY = 6  # next returned nothing
EXIT_VERSION = 7
EXIT_AUTH = 8
EXIT_DAEMON = 9  # client.md §8 — DAEMON_UNAVAILABLE


class ClientError(Exception):
    """Raised when an API call fails; carries exit code + error envelope."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | str | None = None,
        exit_code: int = EXIT_GENERIC,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.details = details or {}
        self.http_status = http_status

    def envelope(self) -> dict[str, Any]:
        """Build ``{"error": {...}}`` for JSON stdout."""
        if isinstance(self.code, ErrorCode):
            return error_body(self.code, self.message, details=self.details)
        code_str = str(self.code) if self.code else "CLIENT_ERROR"
        return {
            "error": {
                "code": code_str,
                "message": self.message,
                "details": self.details,
            }
        }


def exit_code_for_error(code: ErrorCode | str | None, *, http_status: int | None = None) -> int:
    """Map ErrorCode / HTTP status to CLI exit code (cli.md §3.1)."""
    if http_status == 401:
        return EXIT_AUTH
    if isinstance(code, str):
        try:
            code = ErrorCode(code)
        except ValueError:
            code = None
    if isinstance(code, ErrorCode):
        return EXIT_CODE.get(code, EXIT_GENERIC)
    # FastAPI request-validation → PAYLOAD_INVALID (exit 2).
    if http_status == 422:
        return EXIT_USAGE
    return EXIT_GENERIC
