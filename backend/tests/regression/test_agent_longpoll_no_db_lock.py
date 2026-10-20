"""Issue #65: agent opaque token must not hold SQLite write lock during long-poll."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from live_server_fixture import free_port, identify

_AUTH_PASSWORD = "regression-local-secret"
_CSRF_HEADER = "X-CSRF-Token"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@pytest.fixture
def live_server_auth(tmp_path: Path):
    """Live uvicorn with local password so device-grant approve works."""
    port = free_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    db_path = data_dir / "ak5.db"
    jwt_file = tmp_path / "jwt_secret"
    jwt_file.write_text("regression-longpoll-lock-secret\n", encoding="utf-8")
    jwt_file.chmod(0o600)

    env = os.environ.copy()
    env["AK5_SKIP_DOTENV"] = "1"
    env["AK5_DATA_DIR"] = str(data_dir)
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    env["AK5_JWT_SECRET"] = "regression-longpoll-lock-secret"
    env["AK5_JWT_SECRET_FILE"] = str(jwt_file)
    env["HOME"] = str(home)
    env["AK5_PROJECT_ROOT"] = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    env["AK5_DEV_INSECURE_IDENTIFY"] = "1"
    env["AK5_AUTH_PASSWORD"] = _AUTH_PASSWORD
    env.pop("AK5_IDENTIFY_SECRET", None)
    env.pop("IDENTIFY_SECRET", None)
    env.pop("AK5_WEB_PASSWORD", None)
    env.pop("AK5_ACTOR_TOKEN", None)
    env.pop("AK5_ACTOR_ID", None)
    env.pop("AK5_SESSION_FILE", None)

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
        except Exception as exc:  # noqa: BLE001 — wait for listen
            last_err = exc
            time.sleep(0.15)
    else:
        proc.kill()
        out = proc.stdout.read() if proc.stdout else ""
        raise RuntimeError(f"server did not become ready: {last_err}\n{out}")

    yield {"base": base, "api": f"{base}/api/v1"}

    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _mint_agent_opaque(base: str) -> str:
    with httpx.Client(base_url=base, timeout=30.0) as client:
        start = client.post(
            "/oauth/device_authorization",
            json={
                "client_id": "ak5-cli",
                "principal": "agent",
                "name": "longpoll-lock-probe",
                "workflow_role": "worker",
                "capabilities": ["python"],
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
                "workflow_role": "worker",
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
        opaque = token.json()["access_token"]
        assert opaque.startswith("ak5_agt_")
        return opaque


def test_agent_longpoll_does_not_block_concurrent_writes(live_server_auth):
    """Reproduce #65: concurrent ticket create during agent long-poll must be fast + 201,
    and the long-poll itself must finish 200 (not sqlite locked 500).
    """
    base = live_server_auth["base"]
    api = live_server_auth["api"]
    opaque = _mint_agent_opaque(base)
    agent_headers = {
        "Authorization": f"Bearer {opaque}",
        "X-AK5-Consumer": "lock-probe-consumer",
    }
    human_token = identify(
        base,
        actor_id="user_pm",
        actor_type="human",
        role="human",
    )
    human_headers = {"Authorization": f"Bearer {human_token}"}

    with httpx.Client(timeout=30.0) as setup:
        sub = setup.post(
            f"{api}/subscriptions",
            json={
                "filter": {"board_id": "inbox", "events": ["WORK_AVAILABLE"]},
                "start": "now",
            },
            headers=agent_headers,
        )
        assert sub.status_code == 201, sub.text
        sub_id = sub.json()["subscription_id"]

    poll_result: dict[str, object] = {}

    def _long_poll() -> None:
        started = time.monotonic()
        try:
            # Separate client — httpx.Client is not thread-safe.
            with httpx.Client(timeout=20.0) as poll_client:
                resp = poll_client.get(
                    f"{api}/subscriptions/{sub_id}/events",
                    params={"wait": 8, "limit": 10},
                    headers=agent_headers,
                )
            poll_result["status"] = resp.status_code
            poll_result["body"] = resp.text
        except Exception as exc:  # noqa: BLE001 — capture for assertion
            poll_result["error"] = str(exc)
        finally:
            poll_result["elapsed"] = time.monotonic() - started

    thread = threading.Thread(target=_long_poll, daemon=True)
    thread.start()
    time.sleep(0.4)  # ensure long-poll auth + wait has started

    write_started = time.monotonic()
    with httpx.Client(timeout=10.0) as writer:
        created = writer.post(
            f"{api}/tickets",
            json={
                "title": "lock-probe ticket",
                "original_request": "concurrent write during agent long-poll",
            },
            headers=human_headers,
        )
    write_elapsed = time.monotonic() - write_started

    assert created.status_code == 201, created.text
    # Pre-fix: ~3.6s under SQLITE_BUSY_TIMEOUT=5s. Fixed path should be snappy.
    assert write_elapsed < 1.5, f"write blocked for {write_elapsed:.2f}s (SQLite lock?)"

    thread.join(timeout=20)
    assert not thread.is_alive(), "long-poll did not finish"
    assert poll_result.get("error") is None, poll_result
    assert poll_result.get("status") == 200, poll_result
