"""Issue #61: CLI workflow commands use daemon profile tokens (not only .ak5 sessions)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ak5.cli.client_factory import SyncASGITransport, open_client, set_test_transport
from ak5.cli.config import get_api_url, require_auth_headers
from ak5.cli.main import cli
from ak5.client.auth import resolve_access_token
from ak5.client.errors import EXIT_DAEMON, ClientError
from ak5.daemon.credentials import ProfileCredential, save_credential
from ak5.daemon.oauth_flow import set_oauth_test_transport
from ak5.daemon.profiles import Profile, upsert_profile
from ak5.daemon.server import start_daemon_background, stop_daemon
from ak5.server.main import app as gateway_app
from click.testing import CliRunner


@pytest.fixture
def daemon_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    runtime = tmp_path / "runtime" / "ak5"
    runtime.mkdir(parents=True)
    sock = runtime / "daemon.sock"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AK5_HOME", str(home / ".ak5"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("AK5_DAEMON_SOCK", str(sock))
    monkeypatch.setenv("AK5_DAEMON_AUTOSTART", "1")
    monkeypatch.setenv("AK5_PROJECT_ROOT", str(project))
    monkeypatch.delenv("AK5_TOKEN", raising=False)
    monkeypatch.delenv("AK5_ACTOR_TOKEN", raising=False)
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    monkeypatch.delenv("AK5_API_URL", raising=False)
    stop_daemon()
    set_oauth_test_transport(SyncASGITransport(gateway_app))
    set_test_transport(SyncASGITransport(gateway_app))
    yield {"home": home, "sock": sock, "project": project, "server": "http://test/api/v1"}
    set_oauth_test_transport(None)
    set_test_transport(None)
    stop_daemon()


def _seed_profile(
    daemon_home: dict,
    *,
    name: str,
    token: str,
    principal: str = "user",
    role: str = "human",
    actor_id: str = "usr_local_pm",
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
            server=daemon_home["server"],
            principal=principal,
            role=role,
            actor_id=actor_id,
            workspace="ws_local",
        )
    )


def test_resolve_access_token_from_daemon_profile(daemon_home, monkeypatch):
    _seed_profile(daemon_home, name="me", token="jwt-from-daemon")
    monkeypatch.setenv("AK5_PROFILE", "me")
    start_daemon_background(sock=daemon_home["sock"])

    token = resolve_access_token()
    assert token == "jwt-from-daemon"

    headers = require_auth_headers()
    assert headers["Authorization"] == "Bearer jwt-from-daemon"
    assert get_api_url() == daemon_home["server"]


def test_open_client_works_without_project_session(daemon_home, monkeypatch):
    """Repro #61: clean cwd, profile set, no project .ak5 identity."""
    _seed_profile(
        daemon_home,
        name="w1",
        token="ak5_agt_test_token",
        principal="agent",
        role="worker",
        actor_id="agt_w1",
    )
    monkeypatch.setenv("AK5_PROFILE", "w1")
    start_daemon_background(sock=daemon_home["sock"])

    assert not (daemon_home["project"] / ".ak5").exists()

    headers = require_auth_headers()
    assert headers["Authorization"] == "Bearer ak5_agt_test_token"

    with open_client(require_auth=True, skip_version_check=True) as client:
        auth = str(client._client.headers.get("Authorization", ""))
        assert auth == "Bearer ak5_agt_test_token"


def test_whoami_json_is_pure_json(daemon_home, monkeypatch):
    _seed_profile(daemon_home, name="me", token="tok", actor_id="usr_x")
    monkeypatch.setenv("AK5_PROFILE", "me")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "whoami"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["profile"] == "me"
    assert body["principal"] == "user"
    assert "╭" not in result.output


def test_daemon_autostart_disabled_exit_9(daemon_home, monkeypatch):
    _seed_profile(daemon_home, name="me", token="tok")
    monkeypatch.setenv("AK5_PROFILE", "me")
    monkeypatch.setenv("AK5_DAEMON_AUTOSTART", "0")
    stop_daemon()
    with pytest.raises(ClientError) as exc:
        resolve_access_token()
    assert exc.value.exit_code == EXIT_DAEMON
