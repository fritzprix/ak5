"""Profile registry ``~/.ak5/profiles.toml`` (client.md §2)."""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ak5.daemon.paths import profiles_path


@dataclass(frozen=True)
class Profile:
    name: str
    server: str
    workspace: str = ""
    principal: str = "agent"
    role: str = ""
    actor_id: str = ""


def _parse_toml(text: str) -> dict[str, Any]:
    return tomllib.loads(text) if text.strip() else {}


def load_profiles_file(path: Path | None = None) -> dict[str, Any]:
    p = path or profiles_path()
    if not p.is_file():
        return {"default_profile": "", "profiles": {}}
    data = _parse_toml(p.read_text(encoding="utf-8"))
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        # Nested tables under [profiles.x] land as data["profiles"]["x"] when
        # written that way; also accept top-level profile keys via dump format.
        profiles = {}
        for key, val in data.items():
            if key in ("default_profile", "profiles"):
                continue
            if isinstance(val, dict) and "server" in val:
                profiles[key] = val
        data = {"default_profile": data.get("default_profile", ""), "profiles": profiles}
    return {
        "default_profile": str(data.get("default_profile") or ""),
        "profiles": dict(data.get("profiles") or {}),
    }


def save_profiles_file(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or profiles_path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = [f'default_profile = "{data.get("default_profile") or ""}"', ""]
    for name, conf in sorted((data.get("profiles") or {}).items()):
        if not isinstance(conf, dict):
            continue
        lines.append(f"[profiles.{name}]")
        for key in ("server", "workspace", "principal", "role", "actor_id"):
            if key in conf and conf[key] is not None:
                val = str(conf[key]).replace('"', '\\"')
                lines.append(f'{key} = "{val}"')
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
    with contextlib.suppress(OSError):
        p.chmod(0o600)


def list_profiles(path: Path | None = None) -> list[Profile]:
    data = load_profiles_file(path)
    out: list[Profile] = []
    for name, conf in (data.get("profiles") or {}).items():
        if not isinstance(conf, dict):
            continue
        out.append(
            Profile(
                name=name,
                server=str(conf.get("server") or ""),
                workspace=str(conf.get("workspace") or ""),
                principal=str(conf.get("principal") or "agent"),
                role=str(conf.get("role") or ""),
                actor_id=str(conf.get("actor_id") or ""),
            )
        )
    return out


def get_profile(name: str, path: Path | None = None) -> Profile | None:
    for p in list_profiles(path):
        if p.name == name:
            return p
    return None


def upsert_profile(profile: Profile, path: Path | None = None) -> None:
    data = load_profiles_file(path)
    profiles = dict(data.get("profiles") or {})
    profiles[profile.name] = {
        "server": profile.server,
        "workspace": profile.workspace,
        "principal": profile.principal,
        "role": profile.role,
        "actor_id": profile.actor_id,
    }
    data["profiles"] = profiles
    if not data.get("default_profile"):
        data["default_profile"] = profile.name
    save_profiles_file(data, path)


def set_default_profile(name: str, path: Path | None = None) -> None:
    data = load_profiles_file(path)
    if name not in (data.get("profiles") or {}):
        raise KeyError(name)
    data["default_profile"] = name
    save_profiles_file(data, path)


def resolve_profile_name(explicit: str | None = None, path: Path | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("AK5_PROFILE", "").strip()
    if env:
        return env
    data = load_profiles_file(path)
    default = str(data.get("default_profile") or "").strip()
    return default or None
