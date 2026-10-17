"""Regression tests for ``ak5 serve`` bind safety, daemon sibling, and ``web`` alias."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ak5.cli.commands.serve import (
    assert_safe_bind,
    auth_is_configured,
    is_loopback_bind,
    start_daemon_sibling,
    stop_daemon_sibling,
)
from ak5.cli.main import cli
from ak5.main import app, create_app
from click.testing import CliRunner
from fastapi import FastAPI


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
    assert_safe_bind("127.0.0.1", insecure=False)


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


def test_assert_safe_bind_allows_non_loopback_with_web_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret")
    assert_safe_bind("0.0.0.0", insecure=False)


def test_assert_safe_bind_allows_non_loopback_with_identify_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AK5_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("AK5_IDENTIFY_SECRET", "shared-secret")
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


def test_daemon_sibling_start_and_cleanup() -> None:
    """Spawn a substitute long-lived process and ensure terminate cleans it up."""
    child_script = (
        "import time, signal, sys\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "time.sleep(60)\n"
    )
    cmd = [sys.executable, "-c", child_script]
    proc = start_daemon_sibling(cmd)
    try:
        assert proc.poll() is None
        # Give the child a moment to start
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and proc.poll() is not None:
            time.sleep(0.05)
        assert proc.poll() is None, "substitute daemon exited immediately"
    finally:
        stop_daemon_sibling(proc, grace_seconds=2.0)

    assert proc.poll() is not None


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
        proc = start_daemon_sibling(substitute)
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


def test_serve_ui_flag_uses_ui_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
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
    import os

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
