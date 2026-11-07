"""Client daemon IPC · profiles · token broker · local fan-out (#13)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from ak5.cli.main import cli
from ak5.contracts.errors import EXIT_CODE, ErrorCode
from ak5.daemon.credentials import ProfileCredential, save_credential
from ak5.daemon.ipc import daemon_client
from ak5.daemon.paths import daemon_endpoint
from ak5.daemon.profiles import Profile, list_profiles, upsert_profile
from ak5.daemon.server import is_daemon_running, read_pid, start_daemon_background, stop_daemon
from click.testing import CliRunner

WINDOWS = sys.platform == "win32"
posix_hooks = pytest.mark.skipif(WINDOWS, reason="hook commands here are POSIX shell (touch, sleep, $VAR)")
posix_socket = pytest.mark.skipif(WINDOWS, reason="Unix domain socket file; Windows uses a named pipe")
windows_only = pytest.mark.skipif(not WINDOWS, reason="named pipe transport")


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


def _client() -> httpx.Client:
    """Platform transport (UDS at ``sock``, or the named pipe on Windows)."""
    return daemon_client(timeout=5.0)


def _setup_worker_profile() -> None:
    upsert_profile(Profile(name="worker-1", server="http://127.0.0.1:9/api/v1", role="worker"))
    save_credential(
        "worker-1",
        ProfileCredential(access_token="tok-abc", actor_id="agent_w1", status="active"),
    )


def test_daemon_status_token_and_events_over_ipc(daemon_home):
    """Transport round trip on every platform (UDS, or the named pipe on Windows #182)."""
    _setup_worker_profile()
    pid = start_daemon_background(sock=daemon_home["sock"])
    assert pid == read_pid()
    assert is_daemon_running()
    with daemon_client(timeout=5.0) as client:
        status = client.get("/v1/status")
        assert status.status_code == 200
        assert status.json()["pid"] == pid
        token = client.post("/v1/token", json={"profile": "worker-1"})
        assert token.json()["access_token"] == "tok-abc"
        spooled = client.post(
            "/v1/spool",
            json={"profile": "worker-1", "type": "WORK_AVAILABLE", "ticket_id": "TK-1", "data": {}},
        )
        assert spooled.status_code == 200
        events = client.get(
            "/v1/events",
            params={"profile": "worker-1", "cursor": 0, "wait": 0, "events": "WORK_AVAILABLE"},
        ).json()["events"]
        assert [e["ticket_id"] for e in events] == ["TK-1"]
    stop_daemon()
    assert not is_daemon_running()


def _pipe_dacl_sddl(name: str) -> str:
    """DACL of a named pipe as SDDL, read with GetNamedSecurityInfoW."""
    import ctypes
    from ctypes import wintypes

    se_file_object, dacl_information, sddl_revision_1 = 1, 4, 1
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), wintypes.LPVOID,
    ]
    k32.LocalFree.restype = wintypes.LPVOID
    k32.LocalFree.argtypes = [wintypes.LPVOID]

    descriptor = wintypes.LPVOID()
    err = adv.GetNamedSecurityInfoW(name, se_file_object, dacl_information, None, None, None, None, ctypes.byref(descriptor))
    assert err == 0, ctypes.WinError(err)
    try:
        text = wintypes.LPWSTR()
        ok = adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, sddl_revision_1, dacl_information, ctypes.byref(text), None
        )
        assert ok, ctypes.WinError(ctypes.get_last_error())
        try:
            return str(text.value)
        finally:
            k32.LocalFree(text)
    finally:
        k32.LocalFree(descriptor)


def _canonical_sid(sddl_trustee: str) -> str:
    """``S-1-…`` form of an SDDL trustee, expanding aliases like ``LA`` or ``BA``."""
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.ConvertStringSidToSidW.restype = wintypes.BOOL
    adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    adv.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    k32.LocalFree.restype = wintypes.LPVOID
    k32.LocalFree.argtypes = [wintypes.LPVOID]

    sid = wintypes.LPVOID()
    assert adv.ConvertStringSidToSidW(sddl_trustee, ctypes.byref(sid)), ctypes.WinError(ctypes.get_last_error())
    try:
        text = wintypes.LPWSTR()
        assert adv.ConvertSidToStringSidW(sid, ctypes.byref(text)), ctypes.WinError(ctypes.get_last_error())
        try:
            return str(text.value)
        finally:
            k32.LocalFree(text)
    finally:
        k32.LocalFree(sid)


@windows_only
def test_pipe_is_user_only_and_rejects_squatters(daemon_home):
    from ak5.daemon.win32 import PipeTransport, _UserOnlySecurity, create_pipe_instance, current_user_sid

    _setup_worker_profile()
    start_daemon_background(sock=daemon_home["sock"])
    name = daemon_endpoint()

    sddl = _pipe_dacl_sddl(name)
    assert sddl.startswith("D:P"), sddl  # protected: nothing inherited
    aces = sddl[3:].strip("()").split(")(")
    assert len(aces) == 1, sddl
    assert aces[0].startswith("A;"), sddl
    # SDDL may abbreviate well-known accounts (e.g. LA for the built-in Administrator).
    assert _canonical_sid(aces[0].rsplit(";", 1)[1]) == current_user_sid(), sddl

    security = _UserOnlySecurity()
    try:
        with pytest.raises(PermissionError):
            create_pipe_instance(name, security, first=True)
    finally:
        security.free()

    squatted = httpx.Client(transport=PipeTransport(name, expected_server_pid=1), base_url="http://ak5-daemon")
    with squatted, pytest.raises(httpx.ConnectError, match="refusing to connect"):
        squatted.get("/v1/status")


@windows_only
def test_pipe_client_survives_daemon_idle_timeout(daemon_home):
    """uvicorn closes idle connections after 5 s; the pool must not reuse that pipe."""
    _setup_worker_profile()
    start_daemon_background(sock=daemon_home["sock"])
    with daemon_client(timeout=5.0) as client:
        assert client.get("/v1/status").status_code == 200
        time.sleep(6.5)
        assert client.get("/v1/status").status_code == 200


@windows_only
def test_losing_daemon_keeps_the_running_pid_file(daemon_home):
    _setup_worker_profile()
    pid = start_daemon_background(sock=daemon_home["sock"])
    second = subprocess.run(
        [sys.executable, "-m", "ak5.cli.main", "daemon", "run"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert second.returncode != 0
    assert read_pid() == pid
    assert is_daemon_running()


@windows_only
def test_pid_alive_does_not_signal_on_windows():
    from ak5.daemon.server import pid_alive

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        assert pid_alive(child.pid)
        time.sleep(0.3)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait(timeout=10)
    assert not pid_alive(child.pid)


def test_daemon_unavailable_exit_code():
    assert EXIT_CODE[ErrorCode.DAEMON_UNAVAILABLE] == 9


def test_profiles_roundtrip(daemon_home):
    upsert_profile(
        Profile(name="worker-1", server="http://127.0.0.1:8000/api/v1", role="worker")
    )
    rows = list_profiles()
    assert any(p.name == "worker-1" and p.role == "worker" for p in rows)


@posix_hooks
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

    with _client() as client:
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


@posix_socket
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


@posix_hooks
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
    with _client() as client:
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
        with _client() as c:
            results["spool"] = c.post(
                "/v1/spool",
                json={"profile": "worker-1", "type": "WORK_AVAILABLE", "data": {}},
                timeout=30.0,
            ).status_code

    t = threading.Thread(target=_spool)
    t.start()
    time.sleep(0.4)
    with _client() as client:
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


@posix_hooks
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
    with _client() as client:
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


@posix_hooks
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


@posix_hooks
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
    with _client() as client:
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
