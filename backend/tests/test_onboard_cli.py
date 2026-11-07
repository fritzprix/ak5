"""``ak5 onboard``: read-only setup check with next steps for agents."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from ak5.cli.commands import onboard
from ak5.cli.commands.onboard import ServerCheck
from ak5.cli.main import cli
from ak5.contracts.version import API_VERSION
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
    stale_skills: set[Path] | None = None,
    profiles: list[Profile] | None = None,
    logged_in: set[str] | None = None,
) -> None:
    monkeypatch.setattr(onboard, "_probe_server", lambda url: server)
    monkeypatch.setattr(onboard, "is_daemon_running", lambda: daemon)
    monkeypatch.setattr(onboard, "installed_skill_paths", lambda bases: skills or [])
    monkeypatch.setattr(onboard, "skill_md_is_current", lambda path: path not in (stale_skills or set()))
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


def test_boards_step_comes_after_login_and_is_pinned_to_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    remote = "https://kanban.example/api/v1"
    _setup(monkeypatch, server=ServerCheck(api_url=remote, ready=True, api_version="2", compatible=True))
    result = CliRunner().invoke(cli, ["--json", "onboard", "--server", remote])
    assert result.exit_code == 0, result.output
    steps = json.loads(result.output)["next_steps"]
    login = next(i for i, s in enumerate(steps) if f"ak5 login --url {remote}" in s)
    boards = [i for i, s in enumerate(steps) if "boards`" in s]
    assert boards, steps
    assert all(i > login for i in boards)
    assert all("ak5 --profile <NAME> boards" in steps[i] for i in boards)


def test_stale_skill_copy_still_points_to_skill_show(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = Path("/p/.claude/skills/ak5/SKILL.md")
    _setup(
        monkeypatch,
        server=ServerCheck(api_url=API, ready=True, api_version="2", compatible=True),
        skills=[stale],
        stale_skills={stale},
    )
    data = _run_json()
    joined = "\n".join(data["next_steps"])
    assert data["skill_paths"] == []
    assert data["stale_skill_paths"] == [str(stale)]
    assert "ak5 skill show" in joined
    assert "ak5 skill install --force" in joined


def test_corrupt_credential_counts_as_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        monkeypatch,
        server=ServerCheck(api_url=API, ready=True, api_version="2", compatible=True),
        profiles=[Profile(name="worker", server=API, principal="agent", role="worker")],
    )

    def corrupt(name: str) -> ProfileCredential | None:
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(onboard, "load_credential", corrupt)
    assert _run_json()["profiles"][0]["logged_in"] is False


def _transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, httpx.Response(404))

    return httpx.MockTransport(handler)


HTML = httpx.Response(200, text="<!doctype html><html></html>", headers={"content-type": "text/html"})


@pytest.mark.parametrize(
    ("routes", "error_part"),
    [
        pytest.param({"/readyz": HTML, "/meta": HTML}, "JSONDecodeError", id="html-fallback"),
        pytest.param({"/readyz": httpx.Response(200, json={"status": "ready"})}, "/meta returned HTTP 404", id="meta-404"),
        pytest.param(
            {"/readyz": httpx.Response(200, json={}), "/meta": httpx.Response(200, json=["x"])},
            "no api_version",
            id="meta-not-ak5",
        ),
        pytest.param({"/readyz": httpx.Response(503)}, "/readyz returned HTTP 503", id="readyz-503"),
    ],
)
def test_probe_reports_non_ak5_servers_as_not_ready(routes: dict[str, httpx.Response], error_part: str) -> None:
    check = onboard._probe_server(API, transport=_transport(routes))
    assert check.ready is False
    assert check.compatible is None
    assert check.error is not None and error_part in check.error


def test_probe_accepts_an_ak5_server() -> None:
    routes = {
        "/readyz": httpx.Response(200, json={"status": "ready"}),
        "/meta": httpx.Response(200, json={"api_version": API_VERSION, "min_client": "0.0.0"}),
    }
    check = onboard._probe_server(API, transport=_transport(routes))
    assert check.ready is True
    assert check.api_version == API_VERSION
    assert check.compatible is True


def test_malformed_server_url_reports_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboard, "is_daemon_running", lambda: False)
    monkeypatch.setattr(onboard, "list_profiles", list)
    result = CliRunner().invoke(cli, ["--json", "onboard", "--server", "http://[bad/api/v1"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["server"]["ready"] is False
    assert "ValueError" in data["server"]["error"]
    assert len(data["next_steps"]) == 1
    assert data["next_steps"][0].startswith("ASK THE HUMAN")
