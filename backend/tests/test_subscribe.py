import pytest
from ak5.cli.commands.subscribe import (
    execute_subscriber_command,
    extract_event_context,
    render_command_string,
)
from ak5.cli.main import cli
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


def test_render_command_string():
    template = "curl -X POST https://example.com/api?ticket={ticket_id}&event={event} -d '{title}'"
    ctx = {
        "ticket_id": "TK-007",
        "event": "TICKET_DELEGATED",
        "title": "Secret Task",
    }
    rendered = render_command_string(template, ctx)
    assert rendered == "curl -X POST https://example.com/api?ticket=TK-007&event=TICKET_DELEGATED -d 'Secret Task'"


@pytest.mark.asyncio
async def test_execute_subscriber_command(tmp_path):
    output_file = tmp_path / "out.txt"
    # Shell command writing env vars and reading stdin
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


def test_cli_subscribe_dry_run_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["subscribe", "--help"])
    assert result.exit_code == 0
    assert "--exec" in result.output
    assert "--events" in result.output
    assert "--for-agent" in result.output


@pytest.mark.asyncio
async def test_subscription_loop_processes_matching_event(tmp_path, monkeypatch):
    output_file = tmp_path / "handled.txt"
    exec_cmd = f'echo "{{event}}:{{ticket_id}}:{{title}}" > "{output_file}"'

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

        def stream(self, method, url):
            return FakeStream()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    from ak5.cli.commands.subscribe import run_subscription_loop

    await run_subscription_loop(
        api_url="http://test",
        target_board_id="proj-core-engine",
        exec_template=exec_cmd,
        run_once=True,
    )

    assert output_file.exists()
    assert output_file.read_text().strip() == "TICKET_CREATED:TK-777:Auto Trigger"

