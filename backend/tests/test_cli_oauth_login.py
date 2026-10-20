"""Issue #26: daemon OAuth login → credentials → refresh / logout / whoami."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from ak5.cli.client_factory import SyncASGITransport
from ak5.daemon.app import create_daemon_app
from ak5.daemon.credentials import ProfileCredential, load_credential, save_credential
from ak5.daemon.oauth_flow import (
    poll_device_token,
    set_oauth_test_transport,
    start_device_login,
)
from ak5.daemon.paths import credentials_dir
from ak5.daemon.profiles import Profile, upsert_profile
from ak5.daemon.server import stop_daemon
from ak5.server.auth.sessions import CSRF_HEADER, SESSION_COOKIE
from ak5.server.main import app as gateway_app
from ak5.server.seed import ensure_local_tenancy
from click.testing import CliRunner
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


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
    monkeypatch.delenv("AK5_TOKEN", raising=False)
    monkeypatch.delenv("AK5_ACTOR_TOKEN", raising=False)
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    stop_daemon()
    transport = SyncASGITransport(gateway_app)
    set_oauth_test_transport(transport)
    yield {"home": home, "sock": sock, "server": "http://test/api/v1"}
    set_oauth_test_transport(None)
    stop_daemon()


def _daemon_client() -> httpx.Client:
    """In-process daemon (same process as gateway ASGI transport)."""
    return httpx.Client(
        transport=SyncASGITransport(create_daemon_app()),
        base_url="http://ak5-daemon",
        timeout=10.0,
    )


async def _web_login(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert login.status_code == 200
    assert SESSION_COOKIE in login.cookies
    return login.json()["csrf"]


@pytest.mark.asyncio
async def test_oauth_flow_agent_grant_and_credential_mode(
    client: AsyncClient,
    daemon_home,
    monkeypatch: pytest.MonkeyPatch,
    test_db: AsyncSession,
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    server = daemon_home["server"]
    started = start_device_login(
        server,
        principal="agent",
        name="slice-worker",
        workflow_role="worker",
        capabilities=["py"],
    )
    assert started.user_code
    assert started.device_code

    # Still pending
    assert poll_device_token(server, device_code=started.device_code) is None

    csrf = await _web_login(client, monkeypatch)
    approve = await client.post(
        "/oauth/device/approve",
        json={"user_code": started.user_code, "approved": True},
        headers={CSRF_HEADER: csrf},
    )
    assert approve.status_code == 200, approve.text

    bundle = poll_device_token(server, device_code=started.device_code)
    assert bundle is not None
    assert bundle.access_token.startswith("ak5_agt_")
    assert bundle.agent_id.startswith("agt_")

    # Persist like daemon would
    save_credential(
        "worker-1",
        ProfileCredential(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            expires_at=bundle.expires_at,
            actor_id=bundle.agent_id,
            actor_type="agent",
            status="active",
        ),
    )
    upsert_profile(
        Profile(
            name="worker-1",
            server=server,
            principal="agent",
            role="worker",
            actor_id=bundle.agent_id,
        )
    )
    path = credentials_dir() / "worker-1.json"
    assert path.is_file()
    mode = path.stat().st_mode & 0o777
    assert mode & 0o077 == 0  # not group/world readable
    loaded = load_credential("worker-1")
    assert loaded is not None
    assert loaded.access_token == bundle.access_token


@pytest.mark.asyncio
async def test_daemon_login_poll_logout_agent(
    client: AsyncClient,
    daemon_home,
    monkeypatch: pytest.MonkeyPatch,
    test_db: AsyncSession,
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    server = daemon_home["server"]

    with _daemon_client() as d:
        start = d.post(
            "/v1/login",
            json={
                "profile": "worker-1",
                "server": server,
                "principal": "agent",
                "name": "slice-worker",
                "role": "worker",
                "capabilities": ["py"],
            },
        )
        assert start.status_code == 200, start.text
        body = start.json()
        login_id = body["login_id"]
        user_code = body["user_code"]

        pending = d.get(f"/v1/login/{login_id}")
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"

        csrf = await _web_login(client, monkeypatch)
        approve = await client.post(
            "/oauth/device/approve",
            json={"user_code": user_code, "approved": True},
            headers={CSRF_HEADER: csrf},
        )
        assert approve.status_code == 200, approve.text

        done = d.get(f"/v1/login/{login_id}")
        assert done.status_code == 200, done.text
        result = done.json()
        assert result["status"] == "approved"
        assert result["profile"] == "worker-1"

        tok = d.post("/v1/token", json={"profile": "worker-1"})
        assert tok.status_code == 200
        access = tok.json()["access_token"]
        assert access.startswith("ak5_agt_")

        cred_path = credentials_dir() / "worker-1.json"
        assert (cred_path.stat().st_mode & 0o777) & 0o077 == 0

        logout = d.post("/v1/logout", json={"profile": "worker-1"})
        assert logout.status_code == 200
        assert load_credential("worker-1") is None

        denied = d.post("/v1/token", json={"profile": "worker-1"})
        assert denied.status_code == 401


@pytest.mark.asyncio
async def test_daemon_token_refreshes_user_access(
    client: AsyncClient,
    daemon_home,
    monkeypatch: pytest.MonkeyPatch,
    test_db: AsyncSession,
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    server = daemon_home["server"]

    started = start_device_login(server, principal="user")
    csrf = await _web_login(client, monkeypatch)
    approve = await client.post(
        "/oauth/device/approve",
        json={"user_code": started.user_code, "approved": True},
        headers={CSRF_HEADER: csrf},
    )
    assert approve.status_code == 200
    bundle = poll_device_token(server, device_code=started.device_code)
    assert bundle is not None
    assert bundle.refresh_token

    # Force near-expiry so /v1/token refreshes
    near = (datetime.now(UTC) + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_credential(
        "me",
        ProfileCredential(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            expires_at=near,
            actor_id="usr_local_pm",
            actor_type="user",
            status="active",
        ),
    )
    upsert_profile(
        Profile(
            name="me",
            server=server,
            principal="user",
            role="human",
            actor_id="usr_local_pm",
            workspace="ws_local",
        )
    )

    with _daemon_client() as d:
        tok = d.post("/v1/token", json={"profile": "me"})
        assert tok.status_code == 200, tok.text
        new_access = tok.json()["access_token"]
        assert new_access
        refreshed = load_credential("me")
        assert refreshed is not None
        assert refreshed.refresh_token


@pytest.mark.asyncio
async def test_denied_grant_exit_path(
    client: AsyncClient,
    daemon_home,
    monkeypatch: pytest.MonkeyPatch,
    test_db: AsyncSession,
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    server = daemon_home["server"]
    started = start_device_login(
        server,
        principal="agent",
        name="denied-agent",
        workflow_role="worker",
    )
    csrf = await _web_login(client, monkeypatch)
    deny = await client.post(
        "/oauth/device/approve",
        json={"user_code": started.user_code, "approved": False},
        headers={CSRF_HEADER: csrf},
    )
    assert deny.status_code == 200
    with pytest.raises(Exception) as exc:
        poll_device_token(server, device_code=started.device_code)
    assert getattr(exc.value, "exit_code", None) == 8


def test_whoami_shows_daemon_profile(daemon_home):
    from ak5.cli.main import cli

    save_credential(
        "worker-1",
        ProfileCredential(
            access_token="ak5_agt_test",
            actor_id="agt_test",
            actor_type="agent",
            status="active",
            expires_at="2099-01-01T00:00:00Z",
        ),
    )
    upsert_profile(
        Profile(
            name="worker-1",
            server=daemon_home["server"],
            principal="agent",
            role="worker",
            actor_id="agt_test",
        )
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["whoami", "--profile", "worker-1"])
    assert result.exit_code == 0, result.output
    assert "worker-1" in result.output
    assert "agent" in result.output
