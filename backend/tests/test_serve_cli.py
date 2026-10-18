"""Regression tests for ``ak5 serve`` bind safety, daemon sibling, and ``web`` alias."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ak5.cli.commands.serve import (
    _load_dotenv_files,
    assert_dev_insecure_identify_safe,
    assert_safe_bind,
    auth_is_configured,
    is_loopback_bind,
    resolve_uvicorn_proxy_settings,
    start_daemon_sibling,
    stop_daemon_sibling,
    trusted_proxy_ips,
)
from ak5.cli.main import cli
from ak5.main import app, create_app
from click.testing import CliRunner
from fastapi import FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# Repo root: backend/tests/test_serve_cli.py → parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate_serve_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep checkout ``.env`` from mutating process env via ``run_serve``.

    ``_load_dotenv_files`` writes unset keys into ``os.environ`` permanently.
    A developer ``.env`` with ``AK5_AUTH_PASSWORD`` would otherwise flip bind-safety
    assertions and leak into later modules (e.g. ``test_device_auth``).
    """
    monkeypatch.setenv("AK5_SKIP_DOTENV", "1")
    monkeypatch.setattr("ak5.cli.commands.serve._load_dotenv_files", lambda: None)
    for key in (
        "AK5_AUTH_PASSWORD",
        "AK5_WEB_PASSWORD",
        "AK5_AUTH_USERNAME",
        "AK5_IDENTIFY_SECRET",
        "IDENTIFY_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.WEB_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("127.0.0.2", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("example.com", False),
    ],
)
def test_is_loopback_bind(host: str, expected: bool) -> None:
    assert is_loopback_bind(host) is expected


def test_assert_safe_bind_allows_loopback_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")
    assert_safe_bind("127.0.0.1", insecure=False, tailscale_serve=False)


def test_dev_insecure_identify_allows_loopback() -> None:
    assert_dev_insecure_identify_safe(
        "127.0.0.1", dev_insecure_identify=True, tailscale_serve=False
    )


def test_dev_insecure_identify_rejects_tailscale_serve() -> None:
    with pytest.raises(SystemExit) as exc:
        assert_dev_insecure_identify_safe(
            "127.0.0.1", dev_insecure_identify=True, tailscale_serve=True
        )
    assert exc.value.code == 1


def test_dev_insecure_identify_rejects_non_loopback() -> None:
    with pytest.raises(SystemExit) as exc:
        assert_dev_insecure_identify_safe(
            "0.0.0.0", dev_insecure_identify=True, tailscale_serve=False
        )
    assert exc.value.code == 1


def test_assert_safe_bind_rejects_tailscale_serve_on_loopback_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--tailscale-serve publishes to the tailnet even when bind is loopback."""
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.delenv("IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.WEB_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")
    assert not auth_is_configured()
    with pytest.raises(SystemExit) as exc:
        assert_safe_bind("127.0.0.1", insecure=False, tailscale_serve=True)
    assert exc.value.code == 1


def test_assert_safe_bind_allows_tailscale_serve_with_insecure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")
    assert_safe_bind("127.0.0.1", insecure=True, tailscale_serve=True)


def test_assert_safe_bind_rejects_tailscale_serve_with_web_auth_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Password alone is not enough for exposed binds (identity.md §8 → OIDC)."""
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret")
    monkeypatch.delenv("AK5_OIDC_PROVIDERS", raising=False)
    with pytest.raises(SystemExit) as exc:
        assert_safe_bind("127.0.0.1", insecure=False, tailscale_serve=True)
    assert exc.value.code == 1


def test_assert_safe_bind_allows_tailscale_serve_with_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AK5_OIDC_PROVIDERS",
        '[{"id":"mock","issuer":"https://idp.example","client_id":"c"}]',
    )
    assert_safe_bind("127.0.0.1", insecure=False, tailscale_serve=True)


def test_assert_safe_bind_rejects_non_loopback_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.delenv("IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.WEB_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")
    assert not auth_is_configured()
    with pytest.raises(SystemExit) as exc:
        assert_safe_bind("0.0.0.0", insecure=False)
    assert exc.value.code == 1


def test_assert_safe_bind_allows_non_loopback_with_insecure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")
    assert_safe_bind("0.0.0.0", insecure=True)


def test_assert_safe_bind_rejects_non_loopback_with_web_auth_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret")
    monkeypatch.delenv("AK5_OIDC_PROVIDERS", raising=False)
    with pytest.raises(SystemExit) as exc:
        assert_safe_bind("0.0.0.0", insecure=False)
    assert exc.value.code == 1


def test_assert_safe_bind_rejects_non_loopback_with_identify_secret_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("AK5_IDENTIFY_SECRET", "shared-secret")
    monkeypatch.delenv("AK5_OIDC_PROVIDERS", raising=False)
    with pytest.raises(SystemExit) as exc:
        assert_safe_bind("0.0.0.0", insecure=False)
    assert exc.value.code == 1


def test_assert_safe_bind_allows_non_loopback_with_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AK5_OIDC_PROVIDERS",
        '[{"id":"mock","issuer":"https://idp.example","client_id":"c"}]',
    )
    assert_safe_bind("0.0.0.0", insecure=False)


def test_serve_cli_rejects_non_loopback_without_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.WEB_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")

    runner = CliRunner()
    with patch("ak5.cli.commands.serve.uvicorn.run") as run_mock:
        result = runner.invoke(cli, ["serve", "--host", "0.0.0.0", "--port", "18010"])

    assert result.exit_code == 1, result.output
    assert "Refusing to bind" in result.output
    assert "--insecure" in result.output
    run_mock.assert_not_called()


def test_serve_cli_rejects_tailscale_serve_without_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loopback bind + --tailscale-serve must still require auth (or --insecure)."""
    monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.WEB_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")

    runner = CliRunner()
    with (
        patch("ak5.cli.commands.serve.uvicorn.run") as run_mock,
        patch("ak5.cli.commands.serve._prepare_database") as prep_mock,
        patch("ak5.cli.tailscale.enable_tailscale_serve") as ts_mock,
    ):
        result = runner.invoke(cli, ["serve", "--tailscale-serve", "--port", "18020"])

    assert result.exit_code == 1, result.output
    assert "authentication" in result.output.lower() or "Refusing" in result.output
    assert "--insecure" in result.output
    run_mock.assert_not_called()
    prep_mock.assert_not_called()
    ts_mock.assert_not_called()


def test_serve_cli_insecure_allows_non_loopback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AK5_DATA_DIR", str(data))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(data / 'ak5.db').as_posix()}")
    monkeypatch.setattr(
        "ak5.config.settings.DATABASE_URL",
        f"sqlite+aiosqlite:///{(data / 'ak5.db').as_posix()}",
    )
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with (
        patch("ak5.cli.commands.serve.uvicorn.run") as run_mock,
        patch("ak5.cli.commands.serve._prepare_database"),
    ):
        result = runner.invoke(
            cli,
            ["serve", "--host", "0.0.0.0", "--port", "18011", "--insecure"],
        )

    assert result.exit_code == 0, result.output
    assert "insecure" in result.output.lower() or "Warning" in result.output
    run_mock.assert_called_once()
    kwargs = run_mock.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 18011


def test_daemon_sibling_start_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spawn a substitute long-lived process and ensure terminate cleans it up."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AK5_DATA_DIR", str(data))
    child_script = (
        "import time, signal, sys\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "time.sleep(60)\n"
    )
    cmd = [sys.executable, "-c", child_script]
    proc = start_daemon_sibling(cmd, early_exit_check_seconds=0)
    try:
        assert proc.poll() is None
        # Give the child a moment to start
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and proc.poll() is not None:
            time.sleep(0.05)
        assert proc.poll() is None, "substitute daemon exited immediately"
        log_path = data / "daemon" / "serve-daemon.log"
        assert log_path.is_file()
    finally:
        stop_daemon_sibling(proc, grace_seconds=2.0)

    assert proc.poll() is not None


def test_daemon_sibling_warns_on_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AK5_DATA_DIR", str(data))
    cmd = [sys.executable, "-c", "import sys; sys.exit(7)"]
    proc = start_daemon_sibling(cmd, early_exit_check_seconds=0.2)
    assert proc.poll() == 7
    captured = capsys.readouterr()
    assert "exited early" in captured.out.lower() or "Warning" in captured.out
    # Rich may soft-wrap long paths (serve-\ndaemon.log); compare collapsed text.
    out_flat = "".join(captured.out.split())
    assert "serve-daemon.log" in out_flat
    log_path = data / "daemon" / "serve-daemon.log"
    assert log_path.is_file()


def test_serve_daemon_flag_spawns_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AK5_DATA_DIR", str(data))
    monkeypatch.chdir(tmp_path)

    child_script = (
        "import time, signal, sys\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "time.sleep(60)\n"
    )
    substitute = [sys.executable, "-c", child_script]
    started: list[subprocess.Popen[str]] = []

    def fake_start(command: list[str] | None = None) -> subprocess.Popen[str]:
        proc = start_daemon_sibling(substitute, early_exit_check_seconds=0)
        started.append(proc)
        return proc

    def fake_uvicorn(*_args: object, **_kwargs: object) -> None:
        # Simulate server running briefly then stopping.
        assert started and started[0].poll() is None
        return None

    runner = CliRunner()
    with (
        patch("ak5.cli.commands.serve._prepare_database"),
        patch("ak5.cli.commands.serve.start_daemon_sibling", side_effect=fake_start),
        patch("ak5.cli.commands.serve.uvicorn.run", side_effect=fake_uvicorn),
    ):
        result = runner.invoke(cli, ["serve", "--port", "18012", "--daemon"])

    assert result.exit_code == 0, result.output
    assert len(started) == 1
    # finally block should have stopped the sibling
    assert started[0].poll() is not None


def test_web_is_alias_for_serve_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    runner = CliRunner()
    with (
        patch("ak5.cli.commands.serve._prepare_database"),
        patch("ak5.cli.commands.serve.uvicorn.run") as run_mock,
        patch("ak5.cli.commands.serve._print_ui_banner") as banner_mock,
        patch("ak5.cli.commands.serve._open_browser_later") as browser_mock,
    ):
        result = runner.invoke(cli, ["web", "--port", "18013", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert "Deprecated" in result.output or "deprecated" in result.output.lower()
    assert "serve --ui" in result.output
    banner_mock.assert_called_once()
    browser_mock.assert_not_called()
    run_mock.assert_called_once()
    kwargs = run_mock.call_args.kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 18013
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == "127.0.0.1,::1"
    assert kwargs["forwarded_allow_ips"] != "*"


def test_serve_ui_flag_uses_ui_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    runner = CliRunner()
    with (
        patch("ak5.cli.commands.serve._prepare_database"),
        patch("ak5.cli.commands.serve.uvicorn.run") as run_mock,
        patch("ak5.cli.commands.serve._print_ui_banner") as banner_mock,
        patch("ak5.cli.commands.serve._open_browser_later"),
    ):
        result = runner.invoke(cli, ["serve", "--ui", "--port", "18014", "--no-browser"])

    assert result.exit_code == 0, result.output
    banner_mock.assert_called_once()
    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["proxy_headers"] is True
    assert run_mock.call_args.kwargs["forwarded_allow_ips"] == "127.0.0.1,::1"


def test_resolve_proxy_settings_loopback_ui_restricts_trusted_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    enabled, allow = resolve_uvicorn_proxy_settings(
        "127.0.0.1", ui=True, is_ssl=False, tailscale_serve=False
    )
    assert enabled is True
    assert allow == "127.0.0.1,::1"
    assert allow != "*"


def test_resolve_proxy_settings_non_loopback_disables_proxy_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    enabled, allow = resolve_uvicorn_proxy_settings(
        "0.0.0.0", ui=True, is_ssl=False, tailscale_serve=False
    )
    assert enabled is False
    assert allow is None


def test_resolve_proxy_settings_non_loopback_with_trusted_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AK5_TRUSTED_PROXIES", "10.0.0.5")
    enabled, allow = resolve_uvicorn_proxy_settings(
        "0.0.0.0", ui=True, is_ssl=False, tailscale_serve=False
    )
    assert enabled is True
    assert allow is not None
    assert "10.0.0.5" in allow
    assert "127.0.0.1" in allow
    assert "*" not in allow.split(",")


def test_trusted_proxy_ips_merges_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    assert trusted_proxy_ips() == "127.0.0.1,::1"
    monkeypatch.setenv("AK5_TRUSTED_PROXIES", "10.1.2.3, 10.1.2.4")
    assert trusted_proxy_ips() == "127.0.0.1,::1,10.1.2.3,10.1.2.4"


@pytest.mark.asyncio
async def test_non_loopback_client_cannot_spoof_xff_as_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-loopback peer sending X-Forwarded-For must not rewrite request.client.host."""
    from collections.abc import Awaitable, Callable, MutableMapping
    from typing import Any

    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    proxy_headers, allow_ips = resolve_uvicorn_proxy_settings(
        "0.0.0.0", ui=True, is_ssl=True, tailscale_serve=False
    )
    # Serve must not trust every client via forwarded_allow_ips="*".
    assert proxy_headers is False or (allow_ips is not None and allow_ips != "*")

    seen_client: tuple[str, int] | None = None

    async def raw_app(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        nonlocal seen_client
        if scope["type"] != "http":
            return
        client = scope.get("client")
        if isinstance(client, tuple) and len(client) >= 2:
            seen_client = (str(client[0]), int(client[1]))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app_under_test: Callable[
        [
            MutableMapping[str, Any],
            Callable[[], Awaitable[MutableMapping[str, Any]]],
            Callable[[MutableMapping[str, Any]], Awaitable[None]],
        ],
        Awaitable[None],
    ] = raw_app
    if proxy_headers:
        assert allow_ips is not None
        app_under_test = ProxyHeadersMiddleware(raw_app, trusted_hosts=allow_ips)

    peer = ("203.0.113.50", 54321)
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"127.0.0.1")],
        "client": peer,
        "server": ("0.0.0.0", 8000),
    }

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request"}

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    await app_under_test(scope, receive, send)
    assert seen_client is not None
    assert seen_client[0] == "203.0.113.50"


def test_serve_non_loopback_insecure_disables_proxy_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AK5_DATA_DIR", str(data))
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
    monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
    monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with (
        patch("ak5.cli.commands.serve._prepare_database"),
        patch("ak5.cli.commands.serve.uvicorn.run") as run_mock,
        patch("ak5.cli.commands.serve._print_ui_banner"),
        patch("ak5.cli.commands.serve._open_browser_later"),
    ):
        result = runner.invoke(
            cli,
            ["serve", "--host", "0.0.0.0", "--ui", "--port", "18021", "--insecure", "--no-browser"],
        )

    assert result.exit_code == 0, result.output
    kwargs = run_mock.call_args.kwargs
    assert kwargs["proxy_headers"] is False
    assert kwargs.get("forwarded_allow_ips") in (None, "")


def test_cli_serve_help_lists_new_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    for flag in ("--host", "--port", "--ui", "--daemon", "--insecure", "--tailscale-serve", "--reload"):
        assert flag in result.output


def test_cli_web_help_still_works() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["web", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--no-browser" in result.output


def test_create_app_factory_returns_fastapi() -> None:
    application = create_app()
    assert isinstance(application, FastAPI)
    assert isinstance(app, FastAPI)
    # Module-level app remains the default entrypoint for uvicorn ak5.main:app
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/health" in paths
    assert "/meta" in paths


def test_stop_daemon_sibling_noop_for_none_and_exited() -> None:
    stop_daemon_sibling(None)
    finished = MagicMock()
    finished.poll.return_value = 0
    stop_daemon_sibling(finished)
    finished.terminate.assert_not_called()


def test_default_daemon_command_targets_cli_main() -> None:
    from ak5.cli.commands.serve import default_daemon_command

    cmd = default_daemon_command()
    assert cmd[0] == sys.executable
    assert "ak5.cli.main" in cmd
    assert cmd[-2:] == ["daemon", "run"]


def test_stop_daemon_sibling_sends_sigterm() -> None:
    child_script = (
        "import time, signal, sys\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert proc.poll() is None
    stop_daemon_sibling(proc, grace_seconds=2.0)
    assert proc.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)


def test_load_dotenv_respects_ak5_skip_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AK5_SKIP_DOTENV=1`` must prevent checkout ``.env`` from entering os.environ."""
    env_file = tmp_path / ".env"
    env_file.write_text("AK5_AUTH_PASSWORD=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AK5_SKIP_DOTENV", "1")
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)

    _load_dotenv_files()

    assert "AK5_AUTH_PASSWORD" not in os.environ


def test_load_dotenv_loads_when_skip_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AK5_AUTH_PASSWORD=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AK5_SKIP_DOTENV", raising=False)
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)

    _load_dotenv_files()

    assert os.environ.get("AK5_AUTH_PASSWORD") == "from-dotenv"


def test_load_dotenv_does_not_overwrite_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AK5_AUTH_PASSWORD=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AK5_SKIP_DOTENV", raising=False)
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "already-set")

    _load_dotenv_files()

    assert os.environ.get("AK5_AUTH_PASSWORD") == "already-set"


def test_serve_cli_rejects_non_loopback_with_repo_dotenv_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: repo-root ``.env`` with auth password must not bypass bind safety.

    Without ``AK5_SKIP_DOTENV`` / fixture isolation, ``run_serve`` loads the file and
    ``assert_safe_bind`` incorrectly treats auth as configured.
    """
    env_path = _REPO_ROOT / ".env"
    created = False
    previous: str | None = None
    if env_path.is_file():
        previous = env_path.read_text(encoding="utf-8")
    else:
        created = True
    env_path.write_text("AK5_AUTH_PASSWORD=x\n", encoding="utf-8")
    try:
        monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("AK5_SKIP_DOTENV", "1")
        monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
        monkeypatch.delenv("AK5_WEB_PASSWORD", raising=False)
        monkeypatch.delenv("AK5_IDENTIFY_SECRET", raising=False)
        monkeypatch.setattr("ak5.config.settings.AUTH_PASSWORD", "")
        monkeypatch.setattr("ak5.config.settings.WEB_PASSWORD", "")
        monkeypatch.setattr("ak5.config.settings.IDENTIFY_SECRET", "")

        runner = CliRunner()
        with patch("ak5.cli.commands.serve.uvicorn.run") as run_mock:
            result = runner.invoke(cli, ["serve", "--host", "0.0.0.0", "--port", "18030"])

        assert result.exit_code == 1, result.output
        assert "Refusing to bind" in result.output
        run_mock.assert_not_called()
        # Fixture / SKIP must keep process env clean for later tests.
        assert os.environ.get("AK5_AUTH_PASSWORD") in (None, "")
    finally:
        if created:
            env_path.unlink(missing_ok=True)
        elif previous is not None:
            env_path.write_text(previous, encoding="utf-8")


def test_run_serve_does_not_pollute_environ_from_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_serve`` with ``AK5_SKIP_DOTENV=1`` must not inject ``.env`` into os.environ.

    Restores the real ``_load_dotenv_files`` (autouse otherwise no-ops it) so this
    fails on builds that lack the skip switch.
    """
    from ak5.cli.commands.serve import run_serve

    env_file = tmp_path / ".env"
    env_file.write_text("AK5_AUTH_PASSWORD=pollute-me\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("AK5_SKIP_DOTENV", "1")
    # Bypass the module autouse no-op; exercise the real loader + skip switch.
    monkeypatch.setattr("ak5.cli.commands.serve._load_dotenv_files", _load_dotenv_files)

    with (
        patch("ak5.cli.commands.serve._prepare_database"),
        patch("ak5.cli.commands.serve.uvicorn.run"),
    ):
        run_serve(
            host="127.0.0.1",
            port=18031,
            ui=False,
            daemon=False,
            tailscale_serve=False,
            ssl_keyfile=None,
            ssl_certfile=None,
            reload=False,
            insecure=False,
            no_browser=True,
        )

    assert os.environ.get("AK5_AUTH_PASSWORD") in (None, "")
