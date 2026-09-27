"""AK5 CLI session and local identity resolution.

Resolution order (read):
1. AK5_ACTOR_TOKEN (+ optional AK5_ACTOR_ID / AK5_ACTOR_ROLE)
2. AK5_SESSION_FILE
3. AK5_ACTOR_ID -> <project>/.ak5/sessions/<id>.json
4. Exactly one identity claim under .ak5/identity/ (sole-agent convenience)
5. Legacy ~/.ak5_session.json (migrate once into project .ak5/)

When multiple identity claims exist and no env binding is set, callers must not
auto-pick; use list_identity_hints() and ask the agent to confirm.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1"
LEGACY_SESSION_FILE = Path.home() / ".ak5_session.json"
AK5_DIR_NAME = ".ak5"
SESSIONS_DIR_NAME = "sessions"
IDENTITY_DIR_NAME = "identity"
CLI_FALLBACK_ACTOR_ID = "cli_user"

# Bound sources must not silently fall back to cli_user when JWT is missing.
_BOUND_NO_FALLBACK = frozenset({"sole_claim", "env_actor", "session_file"})

# Safe filename segment for actor_id on disk
_ACTOR_FILE_RE = re.compile(r"[^A-Za-z0-9._-]+")

SessionSource = Literal[
    "env_token",
    "session_file",
    "env_actor",
    "sole_claim",
    "legacy",
    "unbound",
]


@dataclass(frozen=True)
class ResolvedSession:
    """Resolved local actor session (may be unbound when ambiguous)."""

    data: dict[str, Any]
    source: SessionSource
    path: Path | None = None
    project_root: Path | None = None
    ambiguous: bool = False
    hints: tuple[dict[str, Any], ...] = ()


def _sanitize_actor_id(actor_id: str) -> str:
    cleaned = _ACTOR_FILE_RE.sub("_", actor_id.strip())
    return cleaned or "unknown"


def find_project_root(start: Path | None = None) -> Path:
    """Resolve project root for .ak5/ storage."""
    env_root = os.environ.get("AK5_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / AK5_DIR_NAME).is_dir() or (candidate / ".git").exists():
            return candidate
    return cur


def ensure_ak5_dir(project_root: Path | None = None) -> Path:
    """Create .ak5/ with a nested .gitignore so tokens are never committed."""
    root = project_root or find_project_root()
    d = root / AK5_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    return d


def ak5_dir(project_root: Path | None = None) -> Path:
    return ensure_ak5_dir(project_root)


def sessions_dir(project_root: Path | None = None) -> Path:
    return ak5_dir(project_root) / SESSIONS_DIR_NAME


def identity_dir(project_root: Path | None = None) -> Path:
    return ak5_dir(project_root) / IDENTITY_DIR_NAME


def session_path_for(actor_id: str, project_root: Path | None = None) -> Path:
    return sessions_dir(project_root) / f"{_sanitize_actor_id(actor_id)}.json"


def identity_path_for(actor_id: str, project_root: Path | None = None) -> Path:
    return identity_dir(project_root) / f"{_sanitize_actor_id(actor_id)}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _write_json(path: Path, data: dict[str, Any], *, mode: int | None = None) -> None:
    """Atomically write JSON (temp + fsync + replace), optionally with restrictive mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is not None:
        with suppress(OSError):
            path.parent.chmod(0o700)

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        if mode is not None:
            with suppress(OSError):
                os.fchmod(tmp_fd, mode)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        if mode is not None:
            with suppress(OSError):
                path.chmod(mode)
    except Exception:
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def list_identity_hints(project_root: Path | None = None) -> list[dict[str, Any]]:
    """Return identity claim hints (no tokens) under .ak5/identity/."""
    root = project_root or find_project_root()
    idir = identity_dir(root)
    if not idir.is_dir():
        return []

    hints: list[dict[str, Any]] = []
    for path in sorted(idir.glob("*.json")):
        data = _read_json(path)
        if not data:
            continue
        actor_id = data.get("actor_id") or path.stem
        hints.append(
            {
                "actor_id": actor_id,
                "role": data.get("role"),
                "actor_type": data.get("actor_type"),
                "capabilities": data.get("capabilities") or [],
                "logged_in_at": data.get("logged_in_at"),
                "api_url": data.get("api_url"),
                "path": str(path),
            }
        )
    return hints


def write_identity_claim(
    *,
    actor_id: str,
    role: str,
    actor_type: str,
    capabilities: list[str] | None = None,
    api_url: str | None = None,
    project_root: Path | None = None,
) -> Path:
    """Write token-free identity claim used as a whoami/login hint."""
    root = project_root or find_project_root()
    ensure_ak5_dir(root)
    path = identity_path_for(actor_id, root)
    payload = {
        "actor_id": actor_id,
        "role": role,
        "actor_type": actor_type,
        "capabilities": capabilities or [],
        "api_url": api_url or DEFAULT_API_URL,
        "logged_in_at": datetime.now(UTC).isoformat(),
    }
    _write_json(path, payload)
    return path


def save_session(
    data: dict[str, Any],
    project_root: Path | None = None,
    *,
    write_claim: bool = True,
) -> Path:
    """Persist JWT session under .ak5/sessions/<actor_id>.json.

    When write_claim is True (default), also refresh the token-free identity
    claim under .ak5/identity/. Automatic CLI fallbacks should pass
    write_claim=False so they do not pollute multi-agent hint resolution.
    """
    root = project_root or find_project_root()
    ensure_ak5_dir(root)
    actor_id = data.get("actor_id")
    if not actor_id or not isinstance(actor_id, str):
        raise ValueError("save_session requires actor_id")

    path = session_path_for(actor_id, root)
    existing = _read_json(path) or {}
    existing.update(data)
    existing["actor_id"] = actor_id
    _write_json(path, existing, mode=0o600)

    if write_claim:
        caps = existing.get("capabilities")
        if isinstance(caps, str):
            cap_list = [c.strip() for c in caps.split(",") if c.strip()]
        elif isinstance(caps, list):
            cap_list = [str(c) for c in caps]
        else:
            cap_list = []

        write_identity_claim(
            actor_id=actor_id,
            role=str(existing.get("role") or "Unknown"),
            actor_type=str(existing.get("actor_type") or "agent"),
            capabilities=cap_list,
            api_url=existing.get("api_url") or DEFAULT_API_URL,
            project_root=root,
        )
    return path


def clear_session(
    actor_id: str | None = None,
    *,
    all_actors: bool = False,
    project_root: Path | None = None,
) -> list[str]:
    """Remove session and/or identity claim files. Returns removed actor ids."""
    root = project_root or find_project_root()
    removed: list[str] = []

    if all_actors:
        for path in list(sessions_dir(root).glob("*.json")) + list(identity_dir(root).glob("*.json")):
            actor = path.stem
            with suppress(OSError):
                path.unlink()
            if actor not in removed:
                removed.append(actor)
        return removed

    if not actor_id:
        raise ValueError("clear_session requires actor_id or all_actors=True")

    for path in (session_path_for(actor_id, root), identity_path_for(actor_id, root)):
        with suppress(OSError):
            if path.exists():
                path.unlink()
                if actor_id not in removed:
                    removed.append(actor_id)
    return removed


def _non_fallback_session_files(project_root: Path) -> list[Path]:
    sdir = sessions_dir(project_root)
    if not sdir.is_dir():
        return []
    return [p for p in sdir.glob("*.json") if p.stem != CLI_FALLBACK_ACTOR_ID]


def _migrate_legacy_if_needed(project_root: Path) -> dict[str, Any] | None:
    """If project has no real actor sessions yet, copy legacy home session into .ak5/."""
    if _non_fallback_session_files(project_root):
        return None
    legacy = _read_json(LEGACY_SESSION_FILE)
    if not legacy or not legacy.get("actor_id") or not legacy.get("token"):
        return None
    save_session(legacy, project_root)
    return legacy


def resolve_session(project_root: Path | None = None) -> ResolvedSession:
    """Resolve the current actor session without guessing among multiple claims."""
    root = project_root or find_project_root()

    env_token = os.environ.get("AK5_ACTOR_TOKEN")
    if env_token:
        actor_id = os.environ.get("AK5_ACTOR_ID") or "env_actor"
        data = {
            "token": env_token,
            "actor_id": actor_id,
            "role": os.environ.get("AK5_ACTOR_ROLE", "Unknown"),
            "actor_type": os.environ.get("AK5_ACTOR_TYPE", "agent"),
            "api_url": os.environ.get("AK5_API_URL") or DEFAULT_API_URL,
        }
        return ResolvedSession(data=data, source="env_token", project_root=root)

    session_file = os.environ.get("AK5_SESSION_FILE")
    if session_file:
        path = Path(session_file).expanduser()
        data = _read_json(path) or {}
        return ResolvedSession(
            data=data,
            source="session_file",
            path=path if path.exists() else None,
            project_root=root,
        )

    env_actor = os.environ.get("AK5_ACTOR_ID")
    if env_actor:
        path = session_path_for(env_actor, root)
        data = _read_json(path) or {}
        if not data.get("actor_id"):
            data = {
                **data,
                "actor_id": env_actor,
                "role": os.environ.get("AK5_ACTOR_ROLE") or data.get("role"),
                "actor_type": os.environ.get("AK5_ACTOR_TYPE") or data.get("actor_type") or "agent",
            }
        return ResolvedSession(data=data, source="env_actor", path=path, project_root=root)

    hints = list_identity_hints(root)
    if len(hints) == 1:
        actor_id = str(hints[0]["actor_id"])
        path = session_path_for(actor_id, root)
        data = _read_json(path) or {}
        if not data.get("actor_id"):
            data = {
                "actor_id": actor_id,
                "role": hints[0].get("role"),
                "actor_type": hints[0].get("actor_type") or "agent",
                "capabilities": hints[0].get("capabilities") or [],
                "api_url": hints[0].get("api_url"),
            }
        return ResolvedSession(
            data=data,
            source="sole_claim",
            path=path if path.exists() else Path(str(hints[0]["path"])),
            project_root=root,
            hints=(hints[0],),
        )

    if len(hints) > 1:
        return ResolvedSession(
            data={},
            source="unbound",
            project_root=root,
            ambiguous=True,
            hints=tuple(hints),
        )

    migrated = _migrate_legacy_if_needed(root)
    if migrated:
        path = session_path_for(str(migrated["actor_id"]), root)
        return ResolvedSession(data=migrated, source="legacy", path=path, project_root=root)

    legacy = _read_json(LEGACY_SESSION_FILE)
    if legacy and legacy.get("token"):
        return ResolvedSession(
            data=legacy,
            source="legacy",
            path=LEGACY_SESSION_FILE,
            project_root=root,
        )

    return ResolvedSession(data={}, source="unbound", project_root=root, hints=tuple(hints))


def load_session() -> dict[str, Any]:
    """Backward-compatible: return resolved session data (may be empty if unbound)."""
    return resolve_session().data


def get_token() -> str | None:
    token = os.environ.get("AK5_ACTOR_TOKEN")
    if token:
        return token
    return resolve_session().data.get("token")


def get_actor_id() -> str | None:
    env_actor = os.environ.get("AK5_ACTOR_ID")
    if env_actor:
        return env_actor
    resolved = resolve_session()
    if resolved.ambiguous:
        return None
    actor_id = resolved.data.get("actor_id")
    return actor_id if isinstance(actor_id, str) else None


def get_api_url() -> str:
    return os.environ.get("AK5_API_URL") or load_session().get("api_url", DEFAULT_API_URL)


def _resolved_token(resolved: ResolvedSession) -> str | None:
    env_token = os.environ.get("AK5_ACTOR_TOKEN")
    if env_token:
        return env_token
    token = resolved.data.get("token")
    return token if isinstance(token, str) else None


def print_identity_hints(resolved: ResolvedSession | None = None, *, err: bool = False) -> None:
    """Print actionable identity hints (shared by whoami / auth failures)."""
    from rich.console import Console
    from rich.table import Table

    console = Console(stderr=err)
    resolved = resolved or resolve_session()
    root = resolved.project_root or find_project_root()
    hints = list(resolved.hints) if resolved.hints else list_identity_hints(root)

    console.print(
        f"[yellow]Ambiguous or unbound local identities under[/yellow] "
        f"[cyan]{root / '.ak5' / 'identity'}[/cyan]"
    )
    if hints:
        table = Table(show_header=True, box=None, padding=(0, 2))
        table.add_column("Actor", style="cyan")
        table.add_column("Role")
        table.add_column("Capabilities")
        for h in hints:
            caps = h.get("capabilities") or []
            caps_str = ", ".join(caps) if isinstance(caps, list) else str(caps)
            table.add_row(f"@{h['actor_id']}", str(h.get("role") or ""), caps_str)
        console.print(table)
    console.print(
        "[dim]Next: export AK5_ACTOR_ID=<id> && ak5 whoami\n"
        "   or: ak5 login --id <id> --role \"...\" --caps \"...\"\n"
        "   or: ak5 logout --id <stale_id>  # clear stale claims[/dim]"
    )


def get_auth_headers(api_url: str | None = None) -> dict[str, str]:
    resolved = resolve_session()
    if resolved.ambiguous:
        return {}

    token = _resolved_token(resolved)
    if token:
        return {"Authorization": f"Bearer {token}"}

    # Bound to a specific actor without JWT — never silently switch to cli_user.
    if resolved.source in _BOUND_NO_FALLBACK:
        return {}

    root = resolved.project_root or find_project_root()
    # Reuse prior cli_user JWT (written with write_claim=False; not visible as a claim).
    cli_data = _read_json(session_path_for(CLI_FALLBACK_ACTOR_ID, root))
    if cli_data and isinstance(cli_data.get("token"), str):
        return {"Authorization": f"Bearer {cli_data['token']}"}

    target_url = api_url or get_api_url()
    try:
        import httpx

        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{target_url}/auth/identify",
                json={
                    "actor_id": CLI_FALLBACK_ACTOR_ID,
                    "actor_type": "human",
                    "name": "CLI User",
                    "role": "PM",
                },
            )
            if resp.is_success:
                data = resp.json()
                token_val = data.get("access_token")
                if token_val:
                    save_session(
                        {
                            "token": token_val,
                            "actor_id": CLI_FALLBACK_ACTOR_ID,
                            "actor_type": "human",
                            "role": "PM",
                            "api_url": target_url,
                            "capabilities": [],
                        },
                        project_root=root,
                        write_claim=False,
                    )
                    return {"Authorization": f"Bearer {token_val}"}
    except Exception:
        pass
    return {}


def require_auth_headers(api_url: str | None = None) -> dict[str, str]:
    """Return Authorization headers or exit with clear identity guidance (no opaque 401)."""
    from rich.console import Console

    console = Console(stderr=True)
    resolved = resolve_session()

    if resolved.ambiguous:
        print_identity_hints(resolved, err=True)
        raise SystemExit(1)

    token = _resolved_token(resolved)
    if not token and resolved.source in _BOUND_NO_FALLBACK:
        actor_id = resolved.data.get("actor_id") or os.environ.get("AK5_ACTOR_ID") or "unknown"
        console.print(
            f"[yellow]Bound as @{actor_id} (source={resolved.source}) but JWT is missing.[/yellow]"
        )
        console.print(
            f"[dim]Re-authenticate:[/dim] ak5 login --id {actor_id} --role \"...\" --caps \"...\""
        )
        raise SystemExit(1)

    headers = get_auth_headers(api_url)
    if not headers.get("Authorization"):
        console.print("[yellow]No authenticated AK5 session.[/yellow]")
        print_identity_hints(resolved, err=True)
        console.print(
            "[dim]Run [bold cyan]ak5 login --id <actor_id> --role <role>[/bold cyan] first.[/dim]"
        )
        raise SystemExit(1)
    return headers


# Back-compat alias used by older docs/tests
SESSION_FILE = LEGACY_SESSION_FILE
