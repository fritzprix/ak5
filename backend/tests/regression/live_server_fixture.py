"""Shared live uvicorn fixture for CLI/SDK regression gates (#29)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def identify(
    base: str,
    *,
    actor_id: str,
    actor_type: str,
    role: str,
    capabilities: list[str] | None = None,
) -> str:
    resp = httpx.post(
        f"{base}/api/v1/auth/identify",
        json={
            "actor_id": actor_id,
            "actor_type": actor_type,
            "name": actor_id,
            "role": role,
            "capabilities": capabilities or [],
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture
def live_server(tmp_path: Path):
    """Spawn uvicorn with isolated HOME / AK5_DATA_DIR / JWT (no checkout .env)."""
    port = free_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    db_path = data_dir / "ak5.db"
    jwt_file = tmp_path / "jwt_secret"
    jwt_file.write_text("regression-live-server-secret\n", encoding="utf-8")
    jwt_file.chmod(0o600)

    env = os.environ.copy()
    env["AK5_SKIP_DOTENV"] = "1"
    env["AK5_DATA_DIR"] = str(data_dir)
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    env["AK5_JWT_SECRET"] = "regression-live-server-secret"
    env["AK5_JWT_SECRET_FILE"] = str(jwt_file)
    env["HOME"] = str(home)
    env["AK5_PROJECT_ROOT"] = str(tmp_path / "project")
    (tmp_path / "project").mkdir()
    env.pop("AK5_AUTH_PASSWORD", None)
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

    yield {
        "base": base,
        "api": f"{base}/api/v1",
        "env": env,
        "port": port,
        "home": home,
        "data_dir": data_dir,
    }

    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
