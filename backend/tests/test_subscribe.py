import pytest
from ak5.cli.commands.subscribe import execute_subscriber_command
from ak5.cli.main import cli
from ak5.services.event_context import (
    build_hook_environ,
    extract_event_context,
    resolve_hook_session_actor,
)
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


def test_resolve_hook_session_actor_prefers_for_agent():
    assert resolve_hook_session_actor(for_agent="agent-qa", created_by="user_pm") == "agent-qa"
    assert resolve_hook_session_actor(for_agent=None, created_by="user_pm") == "user_pm"
    assert resolve_hook_session_actor(for_agent=None, created_by="system") == ""
    assert resolve_hook_session_actor(for_agent="  ", created_by=None) == ""


def test_build_hook_environ_binds_session_not_event_actor():
    ctx = {
        "event": "TICKET_CREATED",
        "event_type": "TICKET_CREATED",
        "board_id": "board-1",
        "ticket_id": "TK-1",
        "title": "t",
        "actor_id": "user_pm",
        "status": "open",
        "summary": "s",
        "data_json": "{}",
    }
    env = build_hook_environ(ctx, session_actor_id="agent-qa", base={})
    assert env["AK5_ACTOR_ID"] == "agent-qa"
    assert env["AK5_EVENT_ACTOR_ID"] == "user_pm"
    assert env["AK5_TICKET_ID"] == "TK-1"


def test_build_hook_environ_preserves_parent_actor_when_unbound():
    ctx = {
        "event": "TICKET_CREATED",
        "event_type": "TICKET_CREATED",
        "board_id": "board-1",
        "ticket_id": "TK-1",
        "title": "t",
        "actor_id": "user_pm",
        "status": "open",
        "summary": "s",
        "data_json": "{}",
    }
    env = build_hook_environ(ctx, session_actor_id="", base={"AK5_ACTOR_ID": "parent-agent"})
    assert env["AK5_ACTOR_ID"] == "parent-agent"
    assert env["AK5_EVENT_ACTOR_ID"] == "user_pm"

    env_empty = build_hook_environ(ctx, session_actor_id="", base={})
    assert "AK5_ACTOR_ID" not in env_empty
    assert env_empty["AK5_EVENT_ACTOR_ID"] == "user_pm"


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
    import sys

    output_file = tmp_path / "out.txt"
    script = tmp_path / "write_env.py"
    # Script file avoids nested-quote hell in cross-platform `python -c` strings
    script.write_text(
        "import os\n"
        f"open(r'{output_file}', 'w').write("
        "' '.join(["
        "os.environ['AK5_EVENT'],"
        "os.environ['AK5_TICKET_ID'],"
        "os.environ['AK5_ACTOR_ID'],"
        "os.environ['AK5_EVENT_ACTOR_ID'],"
        "]))\n",
        encoding="utf-8",
    )
    cmd = f'"{sys.executable}" "{script}"'
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
    code = await execute_subscriber_command(
        cmd,
        ctx,
        {"raw": True},
        pass_stdin=False,
        session_actor_id="agent-worker",
    )
    assert code == 0
    assert output_file.read_text().strip() == "TICKET_CREATED TK-999 agent-worker admin"


@pytest.mark.asyncio
async def test_execute_subscriber_command_passes_stdin(tmp_path):
    import sys

    output_file = tmp_path / "stdin.json"
    script = tmp_path / "copy_stdin.py"
    script.write_text(
        "import sys, shutil\n"
        f"shutil.copyfileobj(sys.stdin.buffer, open(r'{output_file}', 'wb'))\n",
        encoding="utf-8",
    )
    cmd = f'"{sys.executable}" "{script}"'
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
    import sys

    output_file = tmp_path / "handled.txt"
    script = tmp_path / "handle_event.py"
    script.write_text(
        "import os\n"
        f"open(r'{output_file}', 'w').write("
        "'[' + os.environ['AK5_EVENT'] + '] ' + os.environ['AK5_TICKET_ID'] + ':' + os.environ['AK5_TITLE'])\n",
        encoding="utf-8",
    )
    exec_cmd = f'"{sys.executable}" "{script}"'

    # Isolate from developer/CI workspace .ak5 sessions
    monkeypatch.setenv("AK5_PROJECT_ROOT", str(tmp_path))
    for key in ("AK5_ACTOR_ID", "AK5_ACTOR_TOKEN", "AK5_SESSION_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "ak5.cli.commands.subscribe.get_auth_headers",
        lambda api_url=None: {"Authorization": "Bearer test-jwt-token"},
    )

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

    monkeypatch.setenv("AK5_PROJECT_ROOT", str(tmp_path))
    for key in ("AK5_ACTOR_ID", "AK5_ACTOR_TOKEN", "AK5_SESSION_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "ak5.cli.commands.subscribe.get_auth_headers",
        lambda api_url=None: {"Authorization": "Bearer test-jwt-token"},
    )

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


def test_smart_event_and_agent_normalization():
    from ak5.services.event_context import (
        matches_agent_filter,
        matches_event_filter,
        normalize_actor_id,
    )

    # 1. normalize_actor_id
    assert normalize_actor_id("@agent-coding-expert") == "agent-coding-expert"
    assert normalize_actor_id("agent-coding-expert") == "agent-coding-expert"
    assert normalize_actor_id("  @Agent-Worker  ") == "agent-worker"
    assert normalize_actor_id("") == ""
    assert normalize_actor_id(None) == ""

    # 2. matches_agent_filter
    ticket_data = {"assigned_to": "agent-coding-expert"}
    assert matches_agent_filter("@agent-coding-expert", ticket_data) is True
    assert matches_agent_filter("agent-coding-expert", ticket_data) is True
    assert matches_agent_filter("@other-agent", ticket_data) is False
    assert matches_agent_filter(None, ticket_data) is True

    # 3. matches_event_filter with None (default: match all)
    assert matches_event_filter(None, "TICKET_CREATED") is True
    assert matches_event_filter(None, "TICKET_DELEGATED") is True
    assert matches_event_filter(None, "ATTACHMENT_ADDED") is True

    # 4. matches_event_filter with DEFAULT / LIFECYCLE
    assert matches_event_filter("DEFAULT", "TICKET_CREATED") is True
    assert matches_event_filter("DEFAULT", "TICKET_DELEGATED") is True
    assert matches_event_filter("DEFAULT", "TICKET_UPDATED") is True
    assert matches_event_filter("DEFAULT", "TICKET_MOVED") is True
    assert matches_event_filter("DEFAULT", "COMMENT_ADDED") is True
    assert matches_event_filter("DEFAULT", "ATTACHMENT_DELETED") is False

    # 5. TICKET_CREATED automatically covers TICKET_DELEGATED
    assert matches_event_filter("TICKET_CREATED", "TICKET_DELEGATED") is True
    assert matches_event_filter("TICKET_CREATED", "TICKET_MOVED") is False

    # 6. Aliases (e.g. TICKET_COMMENTED -> COMMENT_ADDED)
    assert matches_event_filter("TICKET_COMMENTED", "COMMENT_ADDED") is True

    # 7. Wildcards (TICKET / TICKET_*)
    assert matches_event_filter("TICKET", "TICKET_MOVED") is True
    assert matches_event_filter("TICKET", "TICKET_ARCHIVED") is True
    assert matches_event_filter("TICKET", "COMMENT_ADDED") is True
    assert matches_event_filter("TICKET", "CONNECTED") is False


def test_subscribe_help_guidance():
    """Verify that subscribe --help and create --help contain clear agent guidance."""
    runner = CliRunner()
    res = runner.invoke(cli, ["subscribe", "--help"])
    assert res.exit_code == 0
    assert "DO NOT specify --events unless strictly necessary" in res.output
    assert "TICKET_DELEGATED" in res.output
    assert "TICKET_UPDATED" in res.output
    assert "--for-agent" in res.output

    res_create = runner.invoke(cli, ["subscribe", "create", "--help"])
    assert res_create.exit_code == 0
    assert "RECOMMENDED FOR AGENTS" in res_create.output
    assert "TICKET_DELEGATED" in res_create.output


def test_matches_agent_filter_comment_event():
    from ak5.services.event_context import matches_agent_filter

    # 1. Comment event with nested ticket assigned to agent
    comment_event_data = {
        "board_id": "proj-1",
        "ticket_id": "TK-001",
        "ticket": {"ticket_id": "TK-001", "assigned_to": "agent_coder"},
        "comment": {"content": "Great progress!"},
    }
    assert matches_agent_filter("agent_coder", comment_event_data.get("ticket"), comment_event_data) is True
    assert matches_agent_filter("@agent_coder", comment_event_data.get("ticket"), comment_event_data) is True
    assert matches_agent_filter("other_agent", comment_event_data.get("ticket"), comment_event_data) is False

    # 2. Comment event where agent is mentioned in comment text even if unassigned
    unassigned_comment_event = {
        "board_id": "proj-1",
        "ticket_id": "TK-002",
        "ticket": {"ticket_id": "TK-002", "assigned_to": None},
        "comment": {"content": "Pinging @agent_coder to review this."},
    }
    assert matches_agent_filter("agent_coder", unassigned_comment_event.get("ticket"), unassigned_comment_event) is True
    assert matches_agent_filter("@agent_coder", unassigned_comment_event.get("ticket"), unassigned_comment_event) is True


