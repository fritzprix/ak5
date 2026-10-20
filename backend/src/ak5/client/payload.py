"""Load structured JSON payloads from ``@file.json`` or ``-`` (stdin)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ak5.client.errors import EXIT_USAGE, ClientError
from ak5.contracts.errors import ErrorCode


def _payload_error(message: str, *, details: dict[str, Any] | None = None) -> ClientError:
    return ClientError(
        message,
        code=ErrorCode.PAYLOAD_INVALID,
        exit_code=EXIT_USAGE,
        details=details or {},
    )


def load_json_payload(source: str) -> dict[str, Any] | list[Any]:
    """Parse JSON from ``@path``, ``-`` (stdin), or a raw JSON string.

    Spec: structured payloads use ``@file.json`` or ``-`` (cli.md §4.3).
    """
    if source == "-":
        raw = sys.stdin.read()
    elif source.startswith("@"):
        path = Path(source[1:]).expanduser()
        if not path.is_file():
            raise _payload_error(f"Payload file not found: {path}")
        raw = path.read_text(encoding="utf-8")
    else:
        raw = source

    raw = raw.strip()
    if not raw:
        raise _payload_error("Empty JSON payload")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _payload_error(f"Invalid JSON payload: {exc}") from exc

    if not isinstance(data, (dict, list)):
        raise _payload_error("JSON payload must be an object or array")
    return data


def load_json_object(source: str) -> dict[str, Any]:
    """Like :func:`load_json_payload` but requires a JSON object."""
    data = load_json_payload(source)
    if not isinstance(data, dict):
        raise _payload_error("JSON payload must be an object")
    return data
