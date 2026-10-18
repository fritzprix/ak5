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

    # Prefer purged_through_seq — never the expired cursor (410-loop bug)
    assert (
        _tip_from_expired(
            {"error": {"details": {"cursor": 5, "purged_through_seq": 120}}}
        )
        == 120
    )
    assert _tip_from_expired({"error": {"details": {"oldest_seq": 7}}}) == 7
    assert _tip_from_expired({"error": {"details": {"cursor": 5}}}) == 0


def test_fast_subscriber_not_blocked_by_slow_while_polling(daemon_home, monkeypatch):
    """client.md §4.3: new events must reach fast hooks while a slow hook still runs."""
    sock: Path = daemon_home["sock"]
    monkeypatch.setenv("AK5_HOOK_TIMEOUT", "30")
    upsert_profile(Profile(name="worker-1", server="http://127.0.0.1:9/api/v1", role="worker"))
    save_credential(
        "worker-1",
        ProfileCredential(access_token="tok-abc", actor_id="agent_w1", status="active"),
    )
    start_daemon_background(sock=sock)
    home: Path = daemon_home["home"]
    with _uds_client(sock) as client:
        assert (
            client.post(
                "/v1/subscribers",
                json={
                    "profile": "worker-1",
                    "events": ["WORK_AVAILABLE"],
                    "exec_command": "sleep 12",
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
                    "exec_command": f'touch {home}/fast-$AK5_TICKET_ID',
                },
            ).status_code
            == 200
        )
        # Non-blocking spool (mirrors poller schedule_dispatch)
        assert (
            client.post(
                "/v1/spool",
                json={
                    "profile": "worker-1",
                    "type": "WORK_AVAILABLE",
                    "ticket_id": "TK-1",
                    "data": {},
                    "wait": False,
                },
                timeout=5.0,
            ).status_code
            == 200
        )
        time.sleep(0.4)
        assert (
            client.post(
                "/v1/spool",
                json={
                    "profile": "worker-1",
                    "type": "WORK_AVAILABLE",
                    "ticket_id": "TK-2",
                    "data": {},
                    "wait": False,
                },
                timeout=5.0,
            ).status_code
            == 200
        )
    # Fast hook should see TK-2 within a few seconds — not after sleep 12 finishes
    deadline = time.time() + 5
    target = home / "fast-TK-2"
    while time.time() < deadline and not target.is_file():
        time.sleep(0.05)
    assert target.is_file(), "fast subscriber blocked by slow peer / awaited dispatch"


def test_subscribe_add_is_local_only():
    runner = CliRunner()
    help_r = runner.invoke(cli, ["subscribe", "add", "--help"])
    assert help_r.exit_code == 0
    assert "local daemon subscriber" in help_r.output.lower() or "Never creates" in help_r.output
    assert "--local/--server" not in help_r.output


def test_overlapping_dispatch_runs_each_seq_once(daemon_home, monkeypatch):
    """Overlapping dispatch tasks must not re-run already-advanced seqs."""
    import asyncio

    from ak5.daemon.hooks import dispatch_new_events
    from ak5.daemon.state import DaemonState

    monkeypatch.setenv("AK5_HOOK_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AK5_HOOK_RETRY_BASE", "0")
    home: Path = daemon_home["home"]
    log = home / "hook-seq.log"
    state = DaemonState(home / "overlap.db")
    try:
        state.add_subscriber(
            profile="worker-1",
            events=["WORK_AVAILABLE"],
            # Slow enough that a second dispatch starts mid-flight
            exec_command=(
                f'echo "$AK5_TICKET_ID" >> {log}; sleep 0.4'
            ),
        )
        state.append_spool(
            profile="worker-1",
            event_type="WORK_AVAILABLE",
            payload={"event_id": "evt_a"},
            ticket_id="TK-A",
        )
        state.append_spool(
            profile="worker-1",
            event_type="WORK_AVAILABLE",
            payload={"event_id": "evt_b"},
            ticket_id="TK-B",
        )

        async def _overlap() -> None:
            t1 = asyncio.create_task(dispatch_new_events(state, "worker-1"))
            await asyncio.sleep(0.15)  # mid-flight on first (slow) event
            t2 = asyncio.create_task(dispatch_new_events(state, "worker-1"))
            await asyncio.gather(t1, t2)

        asyncio.run(_overlap())
        lines = [ln.strip() for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert lines.count("TK-A") == 1, lines
        assert lines.count("TK-B") == 1, lines
    finally:
        state.close()


def test_hook_env_carries_subscriber_profile_not_daemon_token(daemon_home, monkeypatch):
    """#78: each hook gets its own AK5_PROFILE; daemon-process tokens never leak in."""
    sock: Path = daemon_home["sock"]
    monkeypatch.setenv("AK5_TOKEN", "daemon-shell-token")  # would override profiles in the CLI
    monkeypatch.setenv("AK5_ACTOR_ID", "daemon-actor")  # must not shadow the event actor
    out = daemon_home["home"]
    for name, role in (("worker-1", "worker"), ("gk-1", "gatekeeper")):
        upsert_profile(Profile(name=name, server="http://127.0.0.1:9/api/v1", role=role))
        save_credential(name, ProfileCredential(access_token=f"tok-{name}", actor_id=name, status="active"))
    start_daemon_background(sock=sock)

    record = 'printf "%s|%s|%s|%s" "$AK5_PROFILE" "${AK5_TOKEN-unset}" "$AK5_SUBSCRIBER_ID" "$AK5_ACTOR_ID"'
    with _uds_client(sock) as client:
        for name in ("worker-1", "gk-1"):
            res = client.post(
                "/v1/subscribers",
                json={
                    "id": f"sub-{name}",
                    "profile": name,
                    "events": ["WORK_AVAILABLE"],
                    "exec_command": f"{record} > {out}/{name}.env",
                },
            )
            assert res.status_code == 200, res.text
        # Explicit assignment in --exec still wins over the injected profile.
        res = client.post(
            "/v1/subscribers",
            json={
                "id": "sub-override",
                "profile": "worker-1",
                "events": ["WORK_AVAILABLE"],
                "exec_command": f'AK5_PROFILE=custom sh -c \'printf "%s" "$AK5_PROFILE"\' > {out}/override.env',
            },
        )
        assert res.status_code == 200, res.text
        for name in ("worker-1", "gk-1"):
            spooled = client.post(
                "/v1/spool",
                json={"profile": name, "type": "WORK_AVAILABLE", "data": {"actor_id": f"usr_{name}"}},
                timeout=30.0,
            )
            assert spooled.status_code == 200, spooled.text

    deadline = time.time() + 15
    files = {n: out / f"{n}.env" for n in ("worker-1", "gk-1", "override")}
    while time.time() < deadline and not all(f.is_file() and f.read_text() for f in files.values()):
        time.sleep(0.1)
    # $AK5_ACTOR_ID is the event actor (events.md §4.1), not the daemon's inherited value.
    assert files["worker-1"].read_text() == "worker-1|unset|sub-worker-1|usr_worker-1"
    assert files["gk-1"].read_text() == "gk-1|unset|sub-gk-1|usr_gk-1"
    assert files["override"].read_text() == "custom"


@pytest.mark.asyncio
async def test_mcp_adapter_gets_token_from_daemon_profile(daemon_home, monkeypatch):
    """client.md §7 (#35): MCP authenticates via AK5_PROFILE → daemon /v1/token."""
    from ak5.mcp.client import AK5Client

    sock: Path = daemon_home["sock"]
    upsert_profile(Profile(name="worker-1", server="http://127.0.0.1:9/api/v1", role="worker"))
    save_credential(
        "worker-1",
        ProfileCredential(access_token="tok-from-daemon", actor_id="agent_w1", status="active"),
    )
    start_daemon_background(sock=sock)
    monkeypatch.setenv("AK5_PROFILE", "worker-1")
    for key in ("AK5_TOKEN", "AK5_ACTOR_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=[])

    client = AK5Client(base_url="http://test/api/v1")
    client._http = httpx.AsyncClient(base_url="http://test/api/v1", transport=httpx.MockTransport(_handler))
    await client._request("GET", "boards")
    assert seen == ["Bearer tok-from-daemon"]
