import pytest
from click.testing import CliRunner
from ak5_cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "AK5 (Agent K5)" in result.output
    assert "login" in result.output
    assert "agents" in result.output
    assert "delegate" in result.output
    assert "board" in result.output
    assert "demo" in result.output


def test_cli_agents_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["agents", "--help"])
    assert result.exit_code == 0
    assert "--cap" in result.output


def test_cli_delegate_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["delegate", "--help"])
    assert result.exit_code == 0
    assert "--to" in result.output
    assert "--title" in result.output
