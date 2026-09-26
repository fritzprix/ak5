import pytest
from ak5.cli.commands.subscribe import execute_subscriber_command
from ak5.cli.main import cli
from ak5.services.event_context import extract_event_context
from click.testing import CliRunner


def test_extract_event_context_ticket_created():
    data = {
        "ticket": {
            "ticket_id": "TK-101",
            "board_id": "proj-alpha",
            "title": "Build Webhook Bridge",
            "status": "open",
            "column_id": "col_todo",
            "assigned_to": "agent_worker",
        },
        "actor_id": "user_pm",
    }
    ctx = extract_event_context("TICKET_CREATED", data)
    assert ctx["event"] == "TICKET_CREATED"
    assert ctx["ticket_id"] == "TK-101"
    assert ctx["board_id"] == "proj-alpha"
    assert ctx["title"] == "Build Webhook Bridge"
    assert ctx["actor_id"] == "user_pm"
    assert "created by @user_pm" in ctx["summary"]
    assert "TK-101" in ctx["data_json"]


def test_extract_event_context_ticket_moved():
    data = {
        "board_id": "proj-alpha",
        "ticket": {
            "ticket_id": "TK-102",
            "title": "Review Feature",
            "column_id": "col_review",
        },
        "from_column": "col_in_progress",
        "to_column": "col_review",
        "actor_id": "agent_coder",
    }
    ctx = extract_event_context("TICKET_MOVED", data)
    assert ctx["ticket_id"] == "TK-102"
    assert ctx["from_column"] == "col_in_progress"
    assert ctx["to_column"] == "col_review"
    assert "moved to col_review by @agent_coder" in ctx["summary"]


def test_extract_event_context_malformed_nested_data():
    data = {
        "ticket": "invalid_string_ticket",
        "comment": 12345,
        "actor_id": "tester",
    }
    ctx = extract_event_context("COMMENT_ADDED", data)
    assert ctx["actor_id"] == "tester"
    assert ctx["ticket_id"] == ""
    assert ctx["board_id"] == ""


@pytest.mark.asyncio
async def test_execute_subscriber_command_uses_env(tmp_path):
    output_file = tmp_path / "out.txt"
    cmd = f'echo "$AK5_EVENT $AK5_TICKET_ID" > "{output_file}"'
    ctx = {
        "event": "TICKET_CREATED",
        "event_type": "TICKET_CREATED",
        "board_id": "board-1",
        "ticket_id": "TK-999",
        "title": "Test Title",
        "actor_id": "admin",
        "status": "open",
        "summary": "Sample summary",
        "data_json": '{"foo": "bar"}',
    }
    code = await execute_subscriber_command(cmd, ctx, {"raw": True}, pass_stdin=False)
    assert code == 0
    assert output_file.read_text().strip() == "TICKET_CREATED TK-999"


@pytest.mark.asyncio
async def test_execute_subscriber_command_passes_stdin(tmp_path):
    output_file = tmp_path / "stdin.json"
    cmd = f'cat > "{output_file}"'
    ctx = {
        "event": "TICKET_CREATED",
        "event_type": "TICKET_CREATED",
        "board_id": "board-1",
        "ticket_id": "TK-1",
        "title": "t",
        "actor_id": "a",
        "status": "open",
        "summary": "s",
        "data_json": "{}",
    }
    payload = {"event": "TICKET_CREATED", "data": {"ticket": {"ticket_id": "TK-1"}}}
    code = await execute_subscriber_command(cmd, ctx, payload, pass_stdin=True)
    assert code == 0
    assert '"TICKET_CREATED"' in output_file.read_text()


def test_cli_subscribe_dry_run_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "create", "--help"])
    assert result.exit_code == 0
    assert "--exec" in result.output
    assert "--events" in result.output
    assert "--for-agent" in result.output
    assert "placeholder" in result.output.lower() or "$AK5_" in result.output or "stdin" in result.output.lower()


def test_cli_subscribe_dry_run_warns_on_legacy_placeholders():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["subscribe", "create", "proj-core-engine", "--exec", "echo {ticket_id}", "--dry-run"],
    )
    assert result.exit_code == 0
    assert "Placeholder tokens are not expanded" in result.output
    assert "Dry-run mode: Subscription was not registered." in result.output


@pytest.mark.asyncio
async def test_subscription_loop_processes_matching_event(tmp_path, monkeypatch):
    output_file = tmp_path / "handled.txt"
    exec_cmd = f'echo "[$AK5_EVENT] $AK5_TICKET_ID:$AK5_TITLE" > "{output_file}"'

    sse_lines = [
        "event: TICKET_CREATED",
        'data: {"board_id": "proj-core-engine", "ticket": {"ticket_id": "TK-777", "title": "Auto Trigger"}}',
        "",
    ]

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url, **kwargs):
            return FakeStream()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    from ak5.cli.commands.subscribe import run_subscription_loop

    await run_subscription_loop(
        api_url="http://test",
        target_board_id="proj-core-engine",
        exec_command=exec_cmd,
        run_once=True,
    )

    assert output_file.exists()
    assert output_file.read_text().strip() == "[TICKET_CREATED] TK-777:Auto Trigger"


@pytest.mark.asyncio
async def test_subscription_loop_skips_unmatched_or_missing_board(tmp_path, monkeypatch):
    output_file = tmp_path / "handled_unmatched.txt"
    exec_cmd = f'echo "$AK5_EVENT:$AK5_TICKET_ID" > "{output_file}"'

    sse_lines = [
        "event: TICKET_CREATED",
        'data: {"board_id": "other-board", "ticket": {"ticket_id": "TK-888", "title": "Other Board Ticket"}}',
        "",
        "event: TICKET_CREATED",
        'data: {"ticket": {"ticket_id": "TK-999", "title": "Missing Board Ticket"}}',
        "",
    ]

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url, **kwargs):
            return FakeStream()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    from ak5.cli.commands.subscribe import run_subscription_loop

    await run_subscription_loop(
        api_url="http://test",
        target_board_id="proj-core-engine",
        exec_command=exec_cmd,
        run_once=True,
    )

    assert not output_file.exists()


def test_subscribe_architecture_requirement():
    """Register & Return: dry-run exits immediately without blocking."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "subscribe",
            "create",
            "proj-core-engine",
            "--exec",
            "echo hello",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Dry-run mode: Subscription was not registered." in result.output
    assert "Hook Command (literal)" in result.output
