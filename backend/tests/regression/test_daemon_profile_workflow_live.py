"""Issue #69: live device-grant → daemon profile → CLI workflow without AK5_TOKEN."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from ak5.contracts.errors import ErrorCode
from ak5.daemon.credentials import ProfileCredential, save_credential
from ak5.daemon.profiles import Profile, upsert_profile
from ak5.daemon.server import start_daemon_background, stop_daemon
from live_server_fixture import free_port

pytestmark = pytest.mark.live_server

REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTH_PASSWORD = "live-daemon-e2e-secret"
_CSRF_HEADER = "X-CSRF-Token"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@pytest.fixture
def live_daemon_server(tmp_path: Path):
    """Live uvicorn + short XDG_RUNTIME_DIR for AF_UNIX + local password."""
    port = free_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Keep HOME/runtime short — sock path must fit AF_UNIX ~108 bytes.
    home = tmp_path / "h"
    home.mkdir()
    runtime = tmp_path / "r"
    runtime.mkdir()
    db_path = data_dir / "ak5.db"
    jwt_file = tmp_path / "jwt"
    jwt_file.write_text("live-daemon-e2e-secret\n", encoding="utf-8")
    jwt_file.chmod(0o600)
    project = tmp_path / "p"
    project.mkdir()
    sock = runtime / "d.sock"

    env = os.environ.copy()
    env["AK5_SKIP_DOTENV"] = "1"
    env["AK5_DATA_DIR"] = str(data_dir)
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    env["AK5_JWT_SECRET"] = "live-daemon-e2e-secret"
    env["AK5_JWT_SECRET_FILE"] = str(jwt_file)
    env["HOME"] = str(home)
    env["AK5_HOME"] = str(home / ".ak5")
    env["AK5_PROJECT_ROOT"] = str(project)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["AK5_DAEMON_SOCK"] = str(sock)
    env["AK5_DAEMON_AUTOSTART"] = "1"
    env["AK5_AUTH_PASSWORD"] = _AUTH_PASSWORD
    # tests/conftest.py autouse enables insecure identify; keep it off so a
    # tokenless CLI request cannot pass as ``cli_user``.
    env.pop("AK5_DEV_INSECURE_IDENTIFY", None)
    env.pop("AK5_TOKEN", None)
    env.pop("AK5_ACTOR_TOKEN", None)
    env.pop("AK5_ACTOR_ID", None)
    env.pop("AK5_SESSION_FILE", None)
    env.pop("AK5_PROFILE", None)
    env.pop("AK5_IDENTIFY_SECRET", None)
    env.pop("IDENTIFY_SECRET", None)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "ak5.server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 45
    last_err: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"uvicorn exited early ({proc.returncode}): {out}")
        try:
            r = httpx.get(f"{base}/meta", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.15)
    else:
        proc.kill()
        out = proc.stdout.read() if proc.stdout else ""
        raise RuntimeError(f"server did not become ready: {last_err}\n{out}")

    stop_daemon()
    yield {
        "base": base,
        "api": f"{base}/api/v1",
        "env": env,
        "home": home,
        "sock": sock,
        "project": project,
        "runtime": runtime,
    }
    stop_daemon()
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _mint_agent_opaque(base: str) -> tuple[str, str]:
    """Approve an agent device grant; return (opaque_token, agent_id)."""
    with httpx.Client(base_url=base, timeout=30.0) as client:
        start = client.post(
            "/oauth/device_authorization",
            json={
                "client_id": "ak5-cli",
                "principal": "agent",
                "name": "live-triage",
                "workflow_role": "triage",
                "capabilities": ["routing"],
            },
        )
        start.raise_for_status()
        device_code = start.json()["device_code"]
        user_code = start.json()["user_code"]

        login = client.post("/auth/local/login", json={"password": _AUTH_PASSWORD})
        login.raise_for_status()
        csrf = login.json()["csrf"]

        approve = client.post(
            "/oauth/device/approve",
            json={
                "user_code": user_code,
                "approved": True,
                "workflow_role": "triage",
                "board_scope": [],
            },
            headers={_CSRF_HEADER: csrf},
        )
        approve.raise_for_status()

        token = client.post(
            "/oauth/token",
            json={
                "grant_type": _DEVICE_GRANT,
                "device_code": device_code,
                "client_id": "ak5-cli",
            },
        )
        token.raise_for_status()
        body = token.json()
        opaque = body["access_token"]
        assert opaque.startswith("ak5_agt_")
        return opaque, str(body["agent_id"])


def _mint_user_jwt(base: str) -> tuple[str, str]:
    """Approve a user device grant; return (access_token, user_id)."""
    with httpx.Client(base_url=base, timeout=30.0) as client:
        start = client.post(
            "/oauth/device_authorization",
            json={"client_id": "ak5-cli", "principal": "user"},
        )
        start.raise_for_status()
        device_code = start.json()["device_code"]
        user_code = start.json()["user_code"]

        login = client.post("/auth/local/login", json={"password": _AUTH_PASSWORD})
        login.raise_for_status()
        csrf = login.json()["csrf"]

        # The approving web session user becomes the token's principal.
        me = client.get("/auth/me")
        me.raise_for_status()
        user_id = str(me.json()["user_id"])

        approve = client.post(
            "/oauth/device/approve",
            json={"user_code": user_code, "approved": True},
            headers={_CSRF_HEADER: csrf},
        )
        approve.raise_for_status()

        token = client.post(
            "/oauth/token",
            json={
                "grant_type": _DEVICE_GRANT,
                "device_code": device_code,
                "client_id": "ak5-cli",
            },
        )
        token.raise_for_status()
        return token.json()["access_token"], user_id


def _seed_profile(
    *,
    name: str,
    server: str,
    token: str,
    principal: str,
    role: str,
    actor_id: str,
) -> None:
    save_credential(
        name,
        ProfileCredential(
            access_token=token,
            refresh_token="",
            expires_at="2099-01-01T00:00:00Z",
            actor_id=actor_id,
            actor_type=principal,
            status="active",
        ),
    )
    upsert_profile(
        Profile(
            name=name,
            server=server,
            principal=principal,
            role=role,
            actor_id=actor_id,
            workspace="ws_local",
        )
    )


def _ak5(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ak5.cli.main", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )


def _cli_env(live: dict, *, profile: str) -> dict[str, str]:
    env = dict(live["env"])
    env["AK5_API_URL"] = live["api"]
    env["AK5_PROFILE"] = profile
    env["AK5_OUTPUT"] = "json"
    env["AK5_SKIP_VERSION_CHECK"] = "1"
    env.pop("AK5_TOKEN", None)
    env.pop("AK5_ACTOR_TOKEN", None)
    env.pop("AK5_ACTOR_ID", None)
    return env


def test_device_login_daemon_profile_workflow_without_env_token(
    live_daemon_server, monkeypatch: pytest.MonkeyPatch
):
    live = live_daemon_server
    user_token, user_id = _mint_user_jwt(live["base"])
    agent_token, agent_id = _mint_agent_opaque(live["base"])

    # Subprocess CLI inherits HOME / AK5_HOME / sock from live env.
    monkeypatch.setenv("HOME", live["env"]["HOME"])
    monkeypatch.setenv("AK5_HOME", live["env"]["AK5_HOME"])
    monkeypatch.setenv("XDG_RUNTIME_DIR", live["env"]["XDG_RUNTIME_DIR"])
    monkeypatch.setenv("AK5_DAEMON_SOCK", live["env"]["AK5_DAEMON_SOCK"])
    monkeypatch.setenv("AK5_PROJECT_ROOT", live["env"]["AK5_PROJECT_ROOT"])

    _seed_profile(
        name="human",
        server=live["api"],
        token=user_token,
        principal="user",
        role="human",
        actor_id=user_id,
    )
    _seed_profile(
        name="triage",
        server=live["api"],
        token=agent_token,
        principal="agent",
        role="triage",
        actor_id=agent_id,
    )
    start_daemon_background(sock=live["sock"])

    created = _ak5(
        _cli_env(live, profile="human"),
        "--json",
        "ticket",
        "create",
        "Daemon-profile live ticket",
    )
    assert created.returncode == 0, created.stdout + created.stderr
    created_body = json.loads(created.stdout)
    tid = created_body["ticket_id"]
    # Identity gate: without the daemon token the CLI would act as ``cli_user``.
    assert created_body["created_by"] == user_id, created_body

    nxt = _ak5(
        _cli_env(live, profile="triage"),
        "--json",
        "next",
        "--inbox",
    )
    assert nxt.returncode == 0, nxt.stdout + nxt.stderr
    next_body = json.loads(nxt.stdout)
    assert next_body.get("ticket_id") == tid or tid in json.dumps(next_body)

    claimed = _ak5(
        _cli_env(live, profile="triage"),
        "--json",
        "ticket",
        "claim",
        tid,
    )
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr
    claim_body = json.loads(claimed.stdout)
    assert "claim_token" in claim_body
    assert claim_body["ticket"]["claimed_by"] == agent_id, claim_body

    # Mutation: profile credential exists but daemon path is off → exit 9 (#69).
    stop_daemon()
    fail_env = _cli_env(live, profile="triage")
    fail_env["AK5_DAEMON_AUTOSTART"] = "0"
    blocked = _ak5(fail_env, "--json", "next", "--inbox")
    assert blocked.returncode == 9, blocked.stdout + blocked.stderr
    try:
        err = json.loads(blocked.stdout)["error"]
    except Exception as parse_exc:  # noqa: BLE001
        raise AssertionError(
            f"stdout={blocked.stdout!r} stderr={blocked.stderr!r}"
        ) from parse_exc
    assert err["code"] == ErrorCode.DAEMON_UNAVAILABLE.value, (
        f"message={err.get('message')!r} full={blocked.stdout!r}"
    )
