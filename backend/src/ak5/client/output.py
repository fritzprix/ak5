"""Stdout contract: ``--json`` / ``AK5_OUTPUT=json`` emits JSON only (cli.md §3)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from ak5.client.errors import ClientError


def is_json_mode(*, flag: bool | None = None) -> bool:
    """True when ``--json`` was set or ``AK5_OUTPUT=json``."""
    if flag is True:
        return True
    if flag is False:
        return os.environ.get("AK5_OUTPUT", "").strip().lower() == "json"
    env = os.environ.get("AK5_OUTPUT", "").strip().lower()
    return env == "json"


def print_result(data: Any, *, json_mode: bool | None = None) -> None:
    """Print success payload: pure JSON when json_mode, else compact JSON for agents."""
    use_json = is_json_mode(flag=json_mode)
    text = json.dumps(data, indent=2 if use_json else None, default=str)
    # Always emit machine-readable success body on stdout (rich is for human extras).
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def print_error(err: ClientError | dict[str, Any], *, json_mode: bool | None = None) -> None:
    """Print error envelope to stdout (json mode) or stderr (human)."""
    if isinstance(err, ClientError):
        envelope = err.envelope()
        exit_code = err.exit_code
        message = err.message
    else:
        envelope = err
        exit_code = 1
        message = str(err.get("error", {}).get("message", err))

    if is_json_mode(flag=json_mode):
        sys.stdout.write(json.dumps(envelope, indent=2, default=str))
        sys.stdout.write("\n")
    else:
        code = envelope.get("error", {}).get("code", "")
        prefix = f"[{code}] " if code else ""
        sys.stderr.write(f"Error: {prefix}{message}\n")

    raise SystemExit(exit_code)


def handle_client_error(exc: BaseException, *, json_mode: bool | None = None) -> None:
    """Normalize exceptions into CLI exit + output."""
    if isinstance(exc, ClientError):
        print_error(exc, json_mode=json_mode)
    if isinstance(exc, SystemExit):
        raise exc
    from ak5.client.errors import EXIT_GENERIC

    print_error(
        ClientError(str(exc), exit_code=EXIT_GENERIC),
        json_mode=json_mode,
    )
