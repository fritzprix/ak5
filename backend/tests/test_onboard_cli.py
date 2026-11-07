"""``ak5 onboard``: read-only setup check with next steps for agents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ak5.cli.commands import onboard
from ak5.cli.commands.onboard import ServerCheck
from ak5.cli.main import cli
from ak5.daemon.credentials import ProfileCredential
from ak5.daemon.profiles import Profile
from click.testing import CliRunner

API = "http://127.0.0.1:8000/api/v1"


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    server: ServerCheck,
    daemon: bool = True,
    skills: list[Path] | None = None,
    profiles: list[Profile] | None = None,
    logged_in: set[str] | None = None,
) -> None:
    monkeypatch.setattr(onboard, "_probe_server", lambda url: server)
    monkeypatch.setattr(onboard, "is_daemon_running", lambda: daemon)
    monkeypatch.setattr(onboard, "installed_skill_paths", lambda bases: skills or [])
    monkeypatch.setattr(onboard, "list_profiles", lambda: profiles or [])

    def fake_credential(name: str) -> ProfileCredential | None:
        if name in (logged_in or set()):
            return ProfileCredential(access_token="ak5_agt_x")
        return None

    monkeypatch.setattr(onboard, "load_credential", fake_credential)


def _run_json() -> dict:
    result = CliRunner().invoke(cli, ["--json", "onboard", "--server", API])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_server_down_only_asks_human_to_start_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, server=ServerCheck(api_url=API, error="ConnectError"))
    steps = _run_json()["next_steps"]
    assert len(steps) == 1
    assert steps[0].startswith("ASK THE HUMAN")
    assert "ak5 up" in steps[0]


def test_incompatible_server_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, server=ServerCheck(api_url=API, ready=True, api_version="3", compatible=False))
    steps = _run_json()["next_steps"]
    assert len(steps) == 1
    assert steps[0].startswith("STOP")


def test_ready_server_walks_through_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        monkeypatch,
        server=ServerCheck(api_url=API, ready=True, api_version="2", compatible=True),
        daemon=False,
    )
    steps = _run_json()["next_steps"]
    joined = "\n".join(steps)
    assert "ak5 daemon start" in steps[0]
    assert "ak5 skill show" in joined
    assert "ak5 skill install" in joined
    assert f"ak5 login --url {API}" in joined
    assert "ASK THE HUMAN" in joined


def test_skips_done_steps_and_offers_reusable_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        monkeypatch,
        server=ServerCheck(api_url=API, ready=True, api_version="2", compatible=True),
        skills=[Path("/p/.claude/skills/ak5/SKILL.md")],
        profiles=[
            Profile(name="worker", server=API, principal="agent", role="worker"),
            Profile(name="me", server=API, principal="human", role="human"),
            Profile(name="remote", server="https://other.example/api/v1", principal="agent", role="worker"),
            Profile(name="stale", server=API, principal="agent", role="gatekeeper"),
        ],
        logged_in={"worker", "me", "remote"},
    )
    data = _run_json()
    joined = "\n".join(data["next_steps"])
    assert "ak5 daemon start" not in joined
    assert "ak5 skill show" not in joined
    assert "reuse: worker." in joined
    assert {p["name"]: p["logged_in"] for p in data["profiles"]} == {
        "worker": True,
        "me": True,
        "remote": True,
        "stale": False,
    }


def test_human_output_lists_status_and_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, server=ServerCheck(api_url=API, ready=True, api_version="2", compatible=True))
    result = CliRunner().invoke(cli, ["onboard", "--server", API])
    assert result.exit_code == 0, result.output
    assert "ready (api 2)" in result.output
    assert "Next steps:" in result.output
    assert "1. " in result.output
