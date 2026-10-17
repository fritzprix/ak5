"""Local claim-token store at ``~/.ak5/claims/<ticket_id>`` (cli.md §4.3)."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from ak5.client.errors import EXIT_USAGE, ClientError


def claims_dir() -> Path:
    """Return ``~/.ak5/claims``, creating it with restrictive permissions."""
    override = os.environ.get("AK5_CLAIMS_DIR")
    d = Path(override).expanduser().resolve() if override else Path.home() / ".ak5" / "claims"
    d.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        d.chmod(0o700)
        d.parent.chmod(0o700)
    return d


def claim_path(ticket_id: str) -> Path:
    safe = ticket_id.strip().replace("/", "_").replace("..", "_")
    if not safe:
        raise ClientError("ticket_id required for claim storage", exit_code=EXIT_USAGE)
    return claims_dir() / safe


def save_claim_token(ticket_id: str, token: str) -> Path:
    path = claim_path(ticket_id)
    path.write_text(token.strip() + "\n", encoding="utf-8")
    with suppress(OSError):
        path.chmod(0o600)
    return path


def load_claim_token(ticket_id: str) -> str | None:
    path = claim_path(ticket_id)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def clear_claim_token(ticket_id: str) -> None:
    path = claim_path(ticket_id)
    with suppress(OSError):
        path.unlink(missing_ok=True)


def resolve_claim_token(ticket_id: str, token: str | None) -> str:
    """Return explicit ``--token`` or fall back to local store."""
    if token:
        return token.strip()
    stored = load_claim_token(ticket_id)
    if stored:
        return stored
    raise ClientError(
        f"No claim token for ticket '{ticket_id}'. "
        "Pass --token or run `ak5 ticket claim` first.",
        exit_code=EXIT_USAGE,
    )
