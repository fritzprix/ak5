"""File-backed credential store (client.md §3.5; keyring deferred to #26)."""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ak5.daemon.paths import credentials_dir


@dataclass
class ProfileCredential:
    access_token: str
    refresh_token: str = ""
    expires_at: str = ""
    actor_id: str = ""
    actor_type: str = "agent"
    status: str = "active"  # active | revoked


def _cred_path(profile: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile)
    return credentials_dir() / f"{safe}.json"


def save_credential(profile: str, cred: ProfileCredential) -> None:
    path = _cred_path(profile)
    path.write_text(json.dumps(asdict(cred), indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def load_credential(profile: str) -> ProfileCredential | None:
    path = _cred_path(profile)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ProfileCredential(
        access_token=str(raw.get("access_token") or ""),
        refresh_token=str(raw.get("refresh_token") or ""),
        expires_at=str(raw.get("expires_at") or ""),
        actor_id=str(raw.get("actor_id") or ""),
        actor_type=str(raw.get("actor_type") or "agent"),
        status=str(raw.get("status") or "active"),
    )


def delete_credential(profile: str) -> None:
    path = _cred_path(profile)
    if path.is_file():
        path.unlink()


def mark_revoked(profile: str) -> None:
    cred = load_credential(profile)
    if cred is None:
        return
    cred.status = "revoked"
    save_credential(profile, cred)
