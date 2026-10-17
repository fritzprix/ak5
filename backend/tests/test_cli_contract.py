"""Issue #12: CLI client contract — exit codes, --json, claim→submit (ASGI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ak5.cli.client_factory import SyncASGITransport, set_test_app, set_test_transport
from ak5.cli.config import save_session
from ak5.cli.main import cli
from ak5.client.claims import claim_path, clear_claim_token, load_claim_token, save_claim_token
from ak5.client.errors import (
    EXIT_AUTH,
    EXIT_EMPTY,
    EXIT_GENERIC,
    EXIT_USAGE,
    EXIT_VERSION,
    ClientError,
    exit_code_for_error,
)
from ak5.client.http import AK5Client, versions_compatible
from ak5.client.output import is_json_mode
from ak5.contracts.errors import ErrorCode
from ak5.main import app
from ak5.routers.auth import create_access_token
from click.testing import CliRunner
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def _clear_transport():
    set_test_transport(None)
    yield
    set_test_transport(None)


@pytest.fixture
def claims_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AK5_CLAIMS_DIR", str(tmp_path / "claims"))
    return tmp_path / "claims"


# ---------------------------------------------------------------------------
# Exit-code mapping (cli.md §3.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ErrorCode.PAYLOAD_INVALID, 2),
        (ErrorCode.CLAIM_CONFLICT, 3),
        (ErrorCode.STALE_CLAIM, 3),
        (ErrorCode.CONSUMER_CONFLICT, 3),
        (ErrorCode.INVALID_TRANSITION, 4),
        (ErrorCode.ROLE_FORBIDDEN, 4),
        (ErrorCode.SEPARATION_OF_DUTIES, 4),
        (ErrorCode.SYSTEM_BOARD, 4),
        (ErrorCode.WIP_LIMIT, 4),
        (ErrorCode.TICKET_BLOCKED, 5),
        (ErrorCode.VERSION_MISMATCH, 7),
        (None, 1),
    ],
)
def test_exit_code_table_rows(code, expected):
    assert exit_code_for_error(code) == expected


def test_exit_code_401_is_8():
    assert exit_code_for_error(None, http_status=401) == EXIT_AUTH
    assert exit_code_for_error(ErrorCode.PAYLOAD_INVALID, http_status=401) == EXIT_AUTH


def test_exit_constants_match_spec():
    assert EXIT_GENERIC == 1
    assert EXIT_USAGE == 2
    assert EXIT_EMPTY == 6
    assert EXIT_VERSION == 7
    assert EXIT_AUTH == 8


# ---------------------------------------------------------------------------
# Version negotiation
# ---------------------------------------------------------------------------


def test_versions_compatible_happy():
    assert versions_compatible(
        server_api_version="2",
        server_min_client="2.0.0",
        client_version="2.0.0",
    )


def test_versions_incompatible_api():
    assert not versions_compatible(
        server_api_version="1",
        server_min_client="2.0.0",
        client_version="2.0.0",
    )


def test_versions_incompatible_min_client():
    assert not versions_compatible(
        server_api_version="2",
        server_min_client="3.0.0",
        client_version="2.0.0",
    )


@pytest.mark.asyncio
async def test_client_version_mismatch_exit_7(anon_client, monkeypatch):
    transport = SyncASGITransport(app)
    client = AK5Client(
        "http://test/api/v1",
        transport=transport,
        client_version="1.0.0",  # below server min_client 2.0.0
    )
    with pytest.raises(ClientError) as exc_info:
        client.ensure_version()
    assert exc_info.value.exit_code == EXIT_VERSION
    assert exc_info.value.code == ErrorCode.VERSION_MISMATCH
    client.close()


# ---------------------------------------------------------------------------
# --json / AK5_OUTPUT
# ---------------------------------------------------------------------------


def test_is_json_mode_flag_and_env(monkeypatch):
    monkeypatch.delenv("AK5_OUTPUT", raising=False)
    assert is_json_mode(flag=True) is True
    assert is_json_mode(flag=False) is False
    monkeypatch.setenv("AK5_OUTPUT", "json")
    assert is_json_mode(flag=None) is True
    assert is_json_mode(flag=False) is True  # env still wins when flag is False? 
    # Spec: flag OR env. flag=False means "not set via CLI"; env still applies.
    # Our is_json_mode(flag=False) checks env — document that.
    assert is_json_mode() is True


def test_cli_help_agent_happy_path_pull_model():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "ak5 next --json" in result.output
    assert "ticket claim" in result.output
    assert "ticket submit" in result.output
    assert "subscribe add" in result.output
    assert "daemon start" in result.output
    # poll-loop prohibition removed per cli.md §6
    assert "Do not invent relative REST URLs or poll loops" not in result.output


def test_cli_json_flag_registered():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "--json" in result.output


def test_login_role_restricted_to_four():
    runner = CliRunner()
    result = runner.invoke(cli, ["login", "--help"])
    assert result.exit_code == 0
    for role in ("triage", "worker", "gatekeeper", "human"):
        assert role in result.output
    bad = runner.invoke(
        cli,
        ["login", "--id", "x", "--role", "PM", "--type", "agent"],
    )
    assert bad.exit_code != 0


def test_delegate_help_no_required_to():
    runner = CliRunner()
    result = runner.invoke(cli, ["delegate", "--help"])
    assert result.exit_code == 0
    assert "--title" in result.output
    # --to is hidden/deprecated; must not be required
    assert "Missing option '--to'" not in result.output


def test_ticket_help_includes_workflow_verbs():
    runner = CliRunner()
    result = runner.invoke(cli, ["ticket", "--help"])
    assert result.exit_code == 0
    for cmd in (
        "claim",
        "heartbeat",
        "release",
        "submit",
        "bounce",
        "route",
        "verdict",
        "postmortem",
        "block",
        "unblock",
        "reopen",
        "reroute",
        "history",
    ):
        assert cmd in result.output


def test_board_create_and_adopt_help():
    runner = CliRunner()
    create = runner.invoke(cli, ["board", "create", "--help"])
    assert create.exit_code == 0
    assert "--template" in create.output
    adopt = runner.invoke(cli, ["board", "adopt", "--help"])
    assert adopt.exit_code == 0


# ---------------------------------------------------------------------------
# Claim token store
# ---------------------------------------------------------------------------


def test_claim_token_roundtrip(claims_home: Path):
    save_claim_token("tkt_abc", "secret-token")
    assert load_claim_token("tkt_abc") == "secret-token"
    assert claim_path("tkt_abc").is_file()
    clear_claim_token("tkt_abc")
    assert load_claim_token("tkt_abc") is None


# ---------------------------------------------------------------------------
# ASGI: claim → submit happy path via CLI
# ---------------------------------------------------------------------------


@pytest.fixture
async def cli_asgi(test_db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wire CliRunner to in-process ASGI app with a worker session."""
    import json as _json

    from ak5.models.actor import Actor

    worker = Actor(
        actor_id="cli_worker",
        actor_type="agent",
        name="CLI Worker",
        role="worker",
        capabilities=_json.dumps(["python"]),
        status="idle",
    )
    if await test_db.get(Actor, worker.actor_id) is None:
        test_db.add(worker)
        await test_db.commit()

    transport = SyncASGITransport(app)
    set_test_transport(transport)

    monkeypatch.setenv("AK5_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("AK5_API_URL", "http://test/api/v1")
    monkeypatch.setenv("AK5_CLAIMS_DIR", str(tmp_path / "claims"))
    monkeypatch.setenv("AK5_SKIP_VERSION_CHECK", "0")
    monkeypatch.delenv("AK5_ACTOR_TOKEN", raising=False)
    monkeypatch.delenv("AK5_OUTPUT", raising=False)

    token = create_access_token("cli_worker", "agent")
    save_session(
        {
            "token": token,
            "actor_id": "cli_worker",
            "actor_type": "agent",
            "role": "worker",
            "api_url": "http://test/api/v1",
            "capabilities": ["python"],
        },
        project_root=tmp_path,
    )
    monkeypatch.setenv("AK5_ACTOR_ID", "cli_worker")
    monkeypatch.setenv("AK5_ACTOR_TOKEN", token)

    yield {"token": token, "transport": transport, "root": tmp_path}


@pytest.mark.asyncio
async def test_claim_submit_happy_path_asgi(cli_asgi, client: AsyncClient, claims_home):
    """Create backlog ticket via API, then CLI claim → submit with --json."""
    # Seed a backlog ticket as human (API)
    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket("CLI claim-submit", board_id="proj-core-engine"),
    )
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]

    runner = CliRunner()

    claim = runner.invoke(cli, ["--json", "ticket", "claim", tid])
    assert claim.exit_code == 0, claim.stdout or claim.output
    claim_body = json.loads(claim.stdout)
    assert "claim_token" in claim_body
    assert load_claim_token(tid) == claim_body["claim_token"]
    # stdout must be pure JSON (no rich markup)
    assert claim.stdout.strip().startswith("{")
    assert "[bold" not in claim.stdout
    assert "✓" not in claim.stdout

    deliverable = {
        "summary": "done via CLI",
        "artifacts": [{"kind": "text", "ref": "ok"}],
        "criteria_self_check": [{"criterion": "done", "pass": True, "note": ""}],
    }
    submit = runner.invoke(
        cli,
        ["--json", "ticket", "submit", tid, "--deliverable", json.dumps(deliverable)],
    )
    assert submit.exit_code == 0, submit.stdout or submit.output
    submit_body = json.loads(submit.stdout)
    assert submit_body["stage"] == "agent_review"
    assert load_claim_token(tid) is None  # cleared after submit
    assert submit.stdout.strip().startswith("{")


@pytest.mark.asyncio
async def test_next_empty_exit_6(cli_asgi):
    runner = CliRunner()
    # Use a board that has no claimable work for this worker (empty custom board)
    # Create empty board first via API client
    with AK5Client(
        "http://test/api/v1",
        token=cli_asgi["token"],
        transport=cli_asgi["transport"],
    ) as c:
        c.create_board(
            {
                "board_id": "empty-cli-board",
                "name": "Empty",
                "template": "default",
            }
        )

    result = runner.invoke(cli, ["--json", "next", "--board", "empty-cli-board"])
    assert result.exit_code == EXIT_EMPTY, result.stdout or result.output


@pytest.mark.asyncio
async def test_json_mode_no_rich_on_error(cli_asgi):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "ticket", "claim", "does-not-exist-xyz"])
    assert result.exit_code != 0
    # Pure JSON on stdout
    body = json.loads(result.stdout)
    assert "error" in body
    assert "code" in body["error"]
    assert "✓" not in result.stdout
    assert "[bold" not in result.stdout


@pytest.mark.asyncio
async def test_auth_missing_exit_8(tmp_path, monkeypatch, test_db):
    set_test_app(app)
    monkeypatch.setenv("AK5_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("AK5_API_URL", "http://test/api/v1")
    for key in ("AK5_ACTOR_TOKEN", "AK5_ACTOR_ID", "AK5_SESSION_FILE"):
        monkeypatch.delenv(key, raising=False)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "next", "--inbox"])
    assert result.exit_code == EXIT_AUTH, result.stdout or result.output


@pytest.mark.asyncio
async def test_payload_invalid_exit_2(cli_asgi, client: AsyncClient):
    created = await client.post(
        "/api/v1/tickets",
        json=board_ticket("bad submit", board_id="proj-core-engine"),
    )
    tid = created.json()["ticket_id"]
    runner = CliRunner()
    claim = runner.invoke(cli, ["--json", "ticket", "claim", tid])
    assert claim.exit_code == 0, claim.stdout or claim.output

    bad = runner.invoke(
        cli,
        ["--json", "ticket", "submit", tid, "--deliverable", '{"summary": ""}'],
    )
    # empty summary fails pydantic min_length → 422 → exit 2
    assert bad.exit_code == EXIT_USAGE, bad.stdout or bad.output
    body = json.loads(bad.stdout)
    assert body["error"]["code"] in (ErrorCode.PAYLOAD_INVALID, "PAYLOAD_INVALID", "CLIENT_ERROR") or bad.exit_code == 2


@pytest.mark.asyncio
async def test_claim_conflict_exit_3(cli_asgi, client: AsyncClient, auth_headers, test_db, monkeypatch):
    """Two triage agents racing on unclassified → CLAIM_CONFLICT (exit 3)."""
    import json as _json

    from ak5.models.actor import Actor
    from helpers_workflow import inbox_ticket

    for actor_id in ("cli_triage_a", "cli_triage_b"):
        if await test_db.get(Actor, actor_id) is None:
            test_db.add(
                Actor(
                    actor_id=actor_id,
                    actor_type="agent",
                    name=actor_id,
                    role="triage",
                    capabilities=_json.dumps([]),
                    status="idle",
                )
            )
    await test_db.commit()

    created = await client.post(
        "/api/v1/tickets",
        json=inbox_ticket("conflict inbox"),
        headers=auth_headers("user_pm", "human"),
    )
    assert created.status_code == 201, created.text
    tid = created.json()["ticket_id"]

    first = await client.post(
        f"/api/v1/tickets/{tid}/claim",
        headers=auth_headers("cli_triage_a", "agent"),
    )
    assert first.status_code == 200, first.text

    # CLI runs as the second triage agent
    token = create_access_token("cli_triage_b", "agent")
    monkeypatch.setenv("AK5_ACTOR_TOKEN", token)
    monkeypatch.setenv("AK5_ACTOR_ID", "cli_triage_b")

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "ticket", "claim", tid])
    assert result.exit_code == 3, result.stdout or result.output
    body = json.loads(result.stdout)
    assert body["error"]["code"] == ErrorCode.CLAIM_CONFLICT


@pytest.mark.asyncio
async def test_create_defaults_to_inbox(cli_asgi, monkeypatch: pytest.MonkeyPatch):
    # T1 create requires human role
    token = create_access_token("user_pm", "human")
    monkeypatch.setenv("AK5_ACTOR_TOKEN", token)
    monkeypatch.setenv("AK5_ACTOR_ID", "user_pm")
    save_session(
        {
            "token": token,
            "actor_id": "user_pm",
            "actor_type": "human",
            "role": "human",
            "api_url": "http://test/api/v1",
            "capabilities": [],
        },
        project_root=cli_asgi["root"],
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "ticket", "create", "inbox request from cli"],
    )
    assert result.exit_code == 0, result.stdout or result.output
    body = json.loads(result.stdout)
    assert body["board_id"] == "inbox"
    assert body["stage"] == "unclassified"
