"""Tests for ``ak5 up``, local profile auto-bootstrap, loopback auto-login, and ``ak5 demo`` v2."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from ak5.cli.commands.demo import run_collaboration_simulation
from ak5.cli.commands.up import bootstrap_local_profiles
from ak5.cli.main import cli
from ak5.daemon.credentials import load_credential
from ak5.daemon.profiles import list_profiles, load_profiles_file
from click.testing import CliRunner


def test_up_help_and_non_loopback_rejected() -> None:
    runner = CliRunner()
    help_res = runner.invoke(cli, ["up", "--help"])
    assert help_res.exit_code == 0
    assert "zero configuration" in help_res.output

    bad_res = runner.invoke(cli, ["up", "--host", "0.0.0.0"])
    assert bad_res.exit_code == 1
    assert "local loopback only" in bad_res.output


def test_global_profile_flag_restores_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AK5_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("AK5_PROFILE", raising=False)

    observed: list[str | None] = []

    @cli.command("_test_profile_probe", hidden=True)
    def _probe() -> None:
        observed.append(os.environ.get("AK5_PROFILE"))

    try:
        runner = CliRunner()
        res = runner.invoke(cli, ["--profile", "worker", "_test_profile_probe"])
        assert res.exit_code == 0
        assert observed == ["worker"]
        assert os.environ.get("AK5_PROFILE") is None
    finally:
        cli.commands.pop("_test_profile_probe", None)


@pytest.mark.asyncio
async def test_bootstrap_local_profiles_idempotent_and_demo_v2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_dir = tmp_path / "ak5-home"
    monkeypatch.setenv("AK5_HOME", str(cfg_dir))

    api_url = "http://127.0.0.1:8000/api/v1"
    first = await bootstrap_local_profiles(api_url)
    assert set(first.keys()) == {"me", "triage", "worker", "gatekeeper"}
    assert first["me"] == "usr_local_pm"

    profiles_data = load_profiles_file()
    assert profiles_data["default_profile"] == "me"
    by_name = {p.name: p for p in list_profiles()}
    assert set(by_name.keys()) == {"me", "triage", "worker", "gatekeeper"}
    assert by_name["me"].principal == "user"
    assert by_name["worker"].role == "worker"

    me_cred_1 = load_credential("me")
    assert me_cred_1 is not None
    assert me_cred_1.refresh_token
    worker_cred_1 = load_credential("worker")
    assert worker_cred_1 is not None
    assert worker_cred_1.access_token.startswith("ak5_agt_")

    # Second run reuses existing active CLI refresh token & agent credential
    second = await bootstrap_local_profiles(api_url)
    assert second == first
    me_cred_2 = load_credential("me")
    assert me_cred_2 is not None
    assert me_cred_2.refresh_token == me_cred_1.refresh_token
    worker_cred_2 = load_credential("worker")
    assert worker_cred_2 is not None
    assert worker_cred_2.access_token == worker_cred_1.access_token

    # Run v2 demo simulation against the in-process ASGI app using bootstrapped credentials
    from ak5.server.main import app

    transport = httpx.ASGITransport(app=app)
    ticket_review = await run_collaboration_simulation(
        api_url,
        auto_approve=False,
        step_delay=0.0,
        transport=transport,
    )
    assert ticket_review["stage"] == "human_review"

    ticket_done = await run_collaboration_simulation(
        api_url,
        auto_approve=True,
        step_delay=0.0,
        transport=transport,
    )
    assert ticket_done["stage"] == "done"


@pytest.mark.asyncio
async def test_local_auto_login_mints_loopback_session_only_when_unguarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ak5.server. database import ensure_database_schema
    from ak5.server.main import app
    from ak5.server.seed import seed_initial_data

    await ensure_database_schema()
    await seed_initial_data()

    monkeypatch.setenv("AK5_LOCAL_AUTO_LOGIN", "1")
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_OIDC_PROVIDERS", raising=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000"
    ) as client:
        me_resp = await client.get("/auth/me")
        assert me_resp.status_code == 200
        body = me_resp.json()
        assert body["authenticated"] is True
        assert body["user_id"] == "usr_local_pm"
        assert "ak5_session" in me_resp.cookies
        assert "ak5_csrf" in me_resp.cookies

    # Also test direct POST /auth/session/token without a prior /auth/me call
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000"
    ) as fresh_client:
        tok_resp = await fresh_client.post("/auth/session/token", json={})
        assert tok_resp.status_code == 200
        tok_data = tok_resp.json()
        assert tok_data["user_id"] == "usr_local_pm"
        assert tok_data["workspace_id"] == "ws_local"
        assert tok_data["access_token"]
        assert "ak5_session" in tok_resp.cookies
        assert "ak5_csrf" in tok_resp.cookies

    # When AK5_AUTH_PASSWORD is set, AK5_LOCAL_AUTO_LOGIN must NOT bypass password login
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "super-secret-password")
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000"
    ) as guarded_client:
        guarded_me = await guarded_client.get("/auth/me")
        assert guarded_me.status_code == 200
        assert guarded_me.json()["authenticated"] is False

        guarded_tok = await guarded_client.post("/auth/session/token", json={})
        assert guarded_tok.status_code == 401
