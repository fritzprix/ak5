"""Client daemon IPC · profiles · token broker · local fan-out (#13)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from ak5.cli.main import cli
from ak5.contracts.errors import EXIT_CODE, ErrorCode
from ak5.daemon.credentials import ProfileCredential, save_credential
from ak5.daemon.profiles import Profile, list_profiles, upsert_profile
from ak5.daemon.server import is_daemon_running, start_daemon_background, stop_daemon
from click.testing import CliRunner
from httpx import HTTPTransport


@pytest.fixture
def daemon_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    runtime = tmp_path / "runtime" / "ak5"
    runtime.mkdir(parents=True)
    sock = runtime / "daemon.sock"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AK5_HOME", str(home / ".ak5"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("AK5_DAEMON_SOCK", str(sock))
    monkeypatch.setenv("AK5_DAEMON_AUTOSTART", "0")
    monkeypatch.setenv("AK5_HOOK_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AK5_HOOK_RETRY_BASE", "0")
    monkeypatch.delenv("AK5_TOKEN", raising=False)
    monkeypatch.delenv("AK5_ACTOR_TOKEN", raising=False)
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    stop_daemon()
    yield {"home": home, "sock": sock}
    stop_daemon()


def _uds_client(sock: Path) -> httpx.Client:
    return httpx.Client(
        transport=HTTPTransport(uds=str(sock)),
        base_url="http://ak5-daemon",
        timeout=5.0,
    )


def test_daemon_unavailable_exit_code():
    assert EXIT_CODE[ErrorCode.DAEMON_UNAVAILABLE] == 9


def test_profiles_roundtrip(daemon_home):
    upsert_profile(
        Profile(name="worker-1", server="http://127.0.0.1:8000/api/v1", role="worker")
    )
    rows = list_profiles()
    assert any(p.name == "worker-1" and p.role == "worker" for p in rows)


def test_daemon_status_token_subscribers_and_waiters(daemon_home):
    sock: Path = daemon_home["sock"]
    upsert_profile(Profile(name="worker-1", server="http://127.0.0.1:9/api/v1", role="worker"))
    save_credential(
        "worker-1",
        ProfileCredential(access_token="tok-abc", actor_id="agent_w1", status="active"),
    )

    pid = start_daemon_background(sock=sock)
    assert pid > 0
    assert is_daemon_running()
    assert sock.exists()
    mode = sock.stat().st_mode & 0o777
    # Best-effort 0600 (umask may differ); at least not world-writable
    assert mode & 0o002 == 0

    with _uds_client(sock) as client:
        st = client.get("/v1/status")
        assert st.status_code == 200
        assert st.json()["pid"] == pid

        tok = client.post("/v1/token", json={"profile": "worker-1"})
        assert tok.status_code == 200
        assert tok.json()["access_token"] == "tok-abc"

        # Three local subscribers
        ids = []
        for i in range(3):
            r = client.post(
                "/v1/subscribers",
                json={
                    "profile": "worker-1",
                    "events": ["WORK_AVAILABLE"],
                    "exec_command": f"touch {daemon_home['home']}/hook-{i}.ok",
                },
            )
            assert r.status_code == 200, r.text
            ids.append(r.json()["id"])

        # Inject event → fans out to hooks + waiters
        inj = client.post(
            "/v1/spool",
            json={
                "profile": "worker-1",
                "type": "WORK_AVAILABLE",
                "ticket_id": "TK-1",
                "board_id": "proj-core-engine",
                "data": {"summary": "work"},
            },
        )
        assert inj.status_code == 200

        for i in range(3):
            assert (daemon_home["home"] / f"hook-{i}.ok").is_file()

        # Waiter sees the event
        ev = client.get(
            "/v1/events",
            params={"profile": "worker-1", "cursor": 0, "wait": 0, "events": "WORK_AVAILABLE"},
        )
        assert ev.status_code == 200
        body = ev.json()
        assert len(body["events"]) >= 1
        assert body["events"][0]["ticket_id"] == "TK-1"

        # One failing hook → dead-letter; others still advance
        bad = client.post(
            "/v1/subscribers",
            json={
                "profile": "worker-1",
                "events": ["WORK_AVAILABLE"],
                "exec_command": "sh -c 'exit 42'",
            },
        ).json()["id"]
        client.post(
            "/v1/spool",
            json={
                "profile": "worker-1",
                "type": "WORK_AVAILABLE",
                "ticket_id": "TK-2",
                "data": {},
            },
        )
        subs = client.get("/v1/subscribers", params={"profile": "worker-1"}).json()
        dead = next(s for s in subs if s["id"] == bad)
        assert dead["dead_letter"]
        ok = next(s for s in subs if s["id"] == ids[0])
        assert ok["local_cursor"] >= 2


def test_cli_daemon_and_profile(daemon_home):
    runner = CliRunner()
    r = runner.invoke(
        cli,
        [
            "profile",
            "use",
            "me",
            "--server",
            "http://127.0.0.1:8000/api/v1",
            "--principal",
            "human",
        ],
    )
    assert r.exit_code == 0, r.output
    listed = runner.invoke(cli, ["--json", "profile", "list"])
    assert listed.exit_code == 0
    body = json.loads(listed.output)
    assert any(p["name"] == "me" for p in body["profiles"])

    start = runner.invoke(cli, ["daemon", "start"])
    assert start.exit_code == 0, start.output
    # Give UDS a moment after background spawn
    deadline = time.time() + 3
    while time.time() < deadline and not is_daemon_running():
        time.sleep(0.05)
    status = runner.invoke(cli, ["--json", "daemon", "status"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.stdout or status.output)
    assert payload["running"] is True
    stop = runner.invoke(cli, ["daemon", "stop"])
    assert stop.exit_code == 0


def test_resolve_access_token_prefers_env(daemon_home, monkeypatch):
    from ak5.client.auth import resolve_access_token

    monkeypatch.setenv("AK5_TOKEN", "env-token")
    assert resolve_access_token() == "env-token"


def test_peer_uid_socket_not_world_accessible(daemon_home):
    sock: Path = daemon_home["sock"]
    upsert_profile(Profile(name="p", server="http://x/api/v1"))
    start_daemon_background(sock=sock)
    assert sock.exists()
    # Directory 0700
    assert (sock.parent.stat().st_mode & 0o077) == 0


def test_peercred_same_uid_ok():
    from ak5.daemon.peercred import require_same_uid

    require_same_uid(None)  # no socket → no-op


def test_next_wait_option_registered():
    runner = CliRunner()
    help_r = runner.invoke(cli, ["next", "--help"])
    assert help_r.exit_code == 0
    assert "--wait" in help_r.output


def test_slow_hook_does_not_block_token(daemon_home, monkeypatch):
    """client.md §4.3: one slow subscriber must not stall IPC (High review finding)."""
    import threading

    sock: Path = daemon_home["sock"]
    monkeypatch.setenv("AK5_HOOK_TIMEOUT", "4")
    upsert_profile(Profile(name="worker-1", server="http://127.0.0.1:9/api/v1", role="worker"))
    save_credential(
        "worker-1",
        ProfileCredential(access_token="tok-abc", actor_id="agent_w1", status="active"),
    )
    start_daemon_background(sock=sock)
    fast_ok = daemon_home["home"] / "fast.ok"
    with _uds_client(sock) as client:
        assert (
            client.post(
                "/v1/subscribers",
                json={
                    "profile": "worker-1",
                    "events": ["WORK_AVAILABLE"],
                    "exec_command": "sleep 8",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/subscribers",
                json={
                    "profile": "worker-1",
                    "events": ["WORK_AVAILABLE"],
                    "exec_command": f"touch {fast_ok}",
                },
            ).status_code
            == 200
        )

    results: dict[str, object] = {}

    def _spool() -> None:
        with _uds_client(sock) as c:
            results["spool"] = c.post(
                "/v1/spool",
                json={"profile": "worker-1", "type": "WORK_AVAILABLE", "data": {}},
                timeout=30.0,
            ).status_code

    t = threading.Thread(target=_spool)
    t.start()
    time.sleep(0.4)
    with _uds_client(sock) as client:
        tok = client.post("/v1/token", json={"profile": "worker-1"}, timeout=2.0)
        assert tok.status_code == 200
        assert tok.json()["access_token"] == "tok-abc"
    t.join(timeout=20)
    assert results.get("spool") == 200
    assert fast_ok.is_file()


def test_cursor_expired_tip_parser():
    from ak5.daemon.poller import _tip_from_expired

    assert (
        _tip_from_expired({"error": {"details": {"cursor": 42}}})
        == 42
    )
    assert _tip_from_expired({"error": {"details": {"oldest_seq": 7}}}) == 7


def test_subscribe_add_is_local_only():
    runner = CliRunner()
    help_r = runner.invoke(cli, ["subscribe", "add", "--help"])
    assert help_r.exit_code == 0
    assert "local daemon subscriber" in help_r.output.lower() or "Never creates" in help_r.output
    assert "--local/--server" not in help_r.output
