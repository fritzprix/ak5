"""Guards: agents must not use blocking SSE watch; humans use `events watch`."""

from __future__ import annotations

from ak5.cli.main import cli
from click.testing import CliRunner


def test_subscribe_help_points_agents_to_create_not_watch():
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "--help"])
    assert result.exit_code == 0
    assert "Register & Return" in result.output
    assert "subscribe add" in result.output
    assert "--for-agent" not in result.output  # removed with server hooks (#11)
    assert "events watch" in result.output
    # Prefer not advertising subscribe watch as a primary example path
    assert "Agents" in result.output or "agents" in result.output.lower()


def test_events_help_exists():
    runner = CliRunner()
    result = runner.invoke(cli, ["events", "--help"])
    assert result.exit_code == 0
    assert "watch" in result.output
    assert "events wait" in result.output
    assert "subscribe add" in result.output


def test_watch_rejected_for_agent_env(monkeypatch):
    monkeypatch.setenv("AK5_AGENT", "1")
    monkeypatch.delenv("AK5_ALLOW_WATCH", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["events", "watch", "proj-core-engine", "--demo-echo"])
    assert result.exit_code == 2
    assert "subscribe add" in result.output
    assert "--for-agent" not in result.output


def test_subscribe_watch_deprecated_and_rejected_for_agents(monkeypatch):
    monkeypatch.setenv("AK5_AGENT", "1")
    monkeypatch.delenv("AK5_ALLOW_WATCH", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "watch", "proj-core-engine", "--demo-echo"])
    assert result.exit_code == 2
    assert "DEPRECATED" in result.output or "deprecated" in result.output.lower() or "subscribe add" in result.output


def test_events_watch_requires_exec_when_allowed(monkeypatch):
    monkeypatch.setenv("AK5_ALLOW_WATCH", "1")
    monkeypatch.delenv("AK5_AGENT", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["events", "watch", "proj-core-engine"])
    assert result.exit_code != 0
    assert "--exec" in result.output or "demo-echo" in result.output


def test_cli_top_level_lists_events():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "events" in result.output
    assert "subscribe" in result.output


def test_root_help_happy_path_uses_real_flags():
    """cli.md §6: the --help happy path must only show flags that exist (#19)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "ak5 login --agent" in result.output
    assert "next --wait" in result.output
    assert "--hook-exec" not in result.output
    assert "--type agent" not in result.output


def test_subscribe_add_preset_without_exec(monkeypatch):
    """--preset alone builds the exec string (#19: documented example must work)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "add", "--preset", "claude", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert 'claude -p "$AK5_SUMMARY"' in result.output


def test_subscribe_add_requires_exec_or_preset():
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "add", "--dry-run"])
    assert result.exit_code == 2
    assert "--preset" in result.output
