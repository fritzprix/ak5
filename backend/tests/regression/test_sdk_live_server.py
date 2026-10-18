"""TypeScript SDK against a real uvicorn process (#29)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_server

REPO_ROOT = Path(__file__).resolve().parents[3]
SDK_ROOT = REPO_ROOT / "packages" / "sdk"
LIVE_SCRIPT = Path(__file__).resolve().parent / "sdk_live_regression.mjs"


@pytest.fixture(scope="module")
def sdk_built():
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm not available")
    dist = SDK_ROOT / "dist" / "index.mjs"
    if not dist.is_file():
        proc = subprocess.run(
            [npm, "ci"],
            cwd=str(SDK_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            pytest.skip(f"npm ci failed: {proc.stderr}")
        build = subprocess.run(
            [npm, "run", "build"],
            cwd=str(SDK_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr
    assert dist.is_file()
    return dist


def test_sdk_create_next_claim_route_submit_history(live_server, sdk_built):
    env = dict(live_server["env"])
    env["AK5_LIVE_BASE_URL"] = live_server["api"]
    env["AK5_DEV_INSECURE_IDENTIFY"] = "1"
    env.pop("AK5_IDENTIFY_SECRET", None)
    env.pop("IDENTIFY_SECRET", None)

    proc = subprocess.run(
        ["node", str(LIVE_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(SDK_ROOT),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = json.loads(proc.stdout.strip().splitlines()[-1])
    assert body["ok"] is True
    assert body["ticket_id"]
