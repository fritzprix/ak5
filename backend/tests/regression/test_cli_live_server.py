"""CLI against a real uvicorn process (#29). Isolates HOME / AK5_DATA_DIR / dotenv."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from ak5.contracts.errors import ErrorCode
from live_server_fixture import identify

pytestmark = pytest.mark.live_server

REPO_ROOT = Path(__file__).resolve().parents[3]


def _ak5(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ak5.cli.main", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )


def _cli_env(live_server: dict, token: str, actor_id: str) -> dict[str, str]:
    env = dict(live_server["env"])
    env["AK5_API_URL"] = live_server["api"]
    env["AK5_ACTOR_TOKEN"] = token
    env["AK5_ACTOR_ID"] = actor_id
    env["AK5_CLAIMS_DIR"] = str(Path(live_server["home"]) / "claims")
    env["AK5_OUTPUT"] = "json"
    env["AK5_SKIP_VERSION_CHECK"] = "1"
    return env


def test_cli_json_exit_codes_against_live_server(live_server):
    human_token = identify(
        live_server["base"],
        actor_id="user_pm",
        actor_type="human",
        role="human",
    )
    triage_a = identify(
        live_server["base"],
        actor_id="live_triage_a",
        actor_type="agent",
        role="triage",
    )
    triage_b = identify(
        live_server["base"],
        actor_id="live_triage_b",
        actor_type="agent",
        role="triage",
    )

    # 401 → exit 8
    bad_auth = _ak5(
        _cli_env(live_server, "not-a-jwt", "nobody"),
        "--json",
        "next",
        "--inbox",
    )
    assert bad_auth.returncode == 8, bad_auth.stdout + bad_auth.stderr
    auth_body = json.loads(bad_auth.stdout)
    assert "error" in auth_body

    # create inbox ticket as human
    created = _ak5(
        _cli_env(live_server, human_token, "user_pm"),
        "--json",
        "ticket",
        "create",
        "Live CLI ticket",
    )
    assert created.returncode == 0, created.stdout + created.stderr
    create_body = json.loads(created.stdout)
    tid = create_body["ticket_id"]
    assert create_body["board_id"] == "inbox"

    # Invalid client payload → exit 2 (pure JSON)
    bad_payload = _ak5(
        _cli_env(live_server, triage_a, "live_triage_a"),
        "--json",
        "ticket",
        "submit",
        tid,
        "--deliverable",
        "{not-json",
    )
    assert bad_payload.returncode == 2, bad_payload.stdout + bad_payload.stderr
    assert "Traceback" not in bad_payload.stdout + bad_payload.stderr
    payload_body = json.loads(bad_payload.stdout)
    assert payload_body["error"]["code"] == ErrorCode.PAYLOAD_INVALID.value

    # Claim conflict → exit 3
    first = httpx.post(
        f"{live_server['api']}/tickets/{tid}/claim",
        headers={"Authorization": f"Bearer {triage_a}"},
        timeout=10,
    )
    assert first.status_code == 200, first.text

    conflict = _ak5(
        _cli_env(live_server, triage_b, "live_triage_b"),
        "--json",
        "ticket",
        "claim",
        tid,
    )
    assert conflict.returncode == 3, conflict.stdout + conflict.stderr
    conflict_body = json.loads(conflict.stdout)
    assert conflict_body["error"]["code"] == ErrorCode.CLAIM_CONFLICT.value
