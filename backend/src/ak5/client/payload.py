"""Load structured JSON payloads from ``@file.json`` or ``-`` (stdin)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ak5.client.errors import EXIT_USAGE, ClientError


def load_json_payload(source: str) -> dict[str, Any] | list[Any]:
    """Parse JSON from ``@path``, ``-`` (stdin), or a raw JSON string.

    Spec: structured payloads use ``@file.json`` or ``-`` (cli.md §4.3).
    """
    if source == "-":
        raw = sys.stdin.read()
    elif source.startswith("@"):
        path = Path(source[1:]).expanduser()
        if not path.is_file():
            raise ClientError(f"Payload file not found: {path}", exit_code=EXIT_USAGE)
        raw = path.read_text(encoding="utf-8")
    else:
        raw = source

    raw = raw.strip()
    if not raw:
        raise ClientError("Empty JSON payload", exit_code=EXIT_USAGE)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClientError(
            f"Invalid JSON payload: {exc}",
            exit_code=EXIT_USAGE,
        ) from exc

    if not isinstance(data, (dict, list)):
        raise ClientError(
            "JSON payload must be an object or array",
            exit_code=EXIT_USAGE,
        )
    return data


def load_json_object(source: str) -> dict[str, Any]:
    """Like :func:`load_json_payload` but requires a JSON object."""
    data = load_json_payload(source)
    if not isinstance(data, dict):
        raise ClientError("JSON payload must be an object", exit_code=EXIT_USAGE)
    return data
