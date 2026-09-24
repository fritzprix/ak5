from ak5.cli.main import cli
from click.testing import CliRunner


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "AK5 (Agent K5)" in result.output
    assert "serve" in result.output
    assert "mcp" in result.output
    assert "login" in result.output
    assert "agents" in result.output
    assert "boards" in result.output
    assert "ticket" in result.output
    assert "delegate" in result.output
    assert "move" in result.output
    assert "comment" in result.output
    assert "create-board" in result.output
    assert "whoami" in result.output
    assert "web" in result.output
    assert "board" in result.output
    assert "demo" in result.output


def test_cli_web_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["web", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--no-browser" in result.output


def test_cli_ticket_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["ticket", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output
    assert "view" in result.output
    assert "move" in result.output
    assert "comment" in result.output
    assert "block" in result.output
    assert "update" in result.output


def test_cli_ticket_create_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["ticket", "create", "--help"])
    assert result.exit_code == 0
    assert "--title" in result.output
    assert "--board" in result.output
    assert "--priority" in result.output


def test_cli_whoami_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["whoami", "--help"])
    assert result.exit_code == 0


def test_cli_create_board_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["create-board", "--help"])
    assert result.exit_code == 0
    assert "--name" in result.output


def test_cli_boards_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["boards", "--help"])
    assert result.exit_code == 0
    assert "--query" in result.output


def test_cli_board_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["board", "--help"])
    assert result.exit_code == 0
    assert "--board-id" in result.output
    assert "--list" in result.output
    assert "[BOARD_ID]" in result.output


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


