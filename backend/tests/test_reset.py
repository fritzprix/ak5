import uuid

import pytest
from ak5.cli.main import cli
from ak5.database import AsyncSessionLocal, engine
from ak5.models.actor import Actor
from ak5.models.base import Base
from ak5.models.board import Board
from click.testing import CliRunner


@pytest.mark.asyncio
async def test_reset_command_interactive_abort():
    runner = CliRunner()
    # User responds "n" to confirmation prompt
    result = runner.invoke(cli, ["reset"], input="n\n")
    assert result.exit_code == 0
    assert "Reset cancelled." in result.output


@pytest.mark.asyncio
async def test_reset_command_with_yes():
    # CI / fresh installs have no schema yet; reset itself create_all's, but the
    # pre-check insert below needs tables first.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_id = f"test_agent_{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as db:
        dummy_actor = Actor(
            actor_id=test_id,
            actor_type="agent",
            name="Temporary Agent",
            role="Worker",
        )
        db.add(dummy_actor)
        await db.commit()

    runner = CliRunner()
    result = runner.invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 0
    assert "AK5 Kanban data successfully reset to initial state!" in result.output
    assert "@user_pm" in result.output
    assert "proj-core-engine" in result.output

    # Verify database state after reset
    async with AsyncSessionLocal() as db:
        # Default entities should exist
        pm = await db.get(Actor, "user_pm")
        assert pm is not None

        board = await db.get(Board, "proj-core-engine")
        assert board is not None

        # Custom dummy actor should have been removed
        temp = await db.get(Actor, test_id)
        assert temp is None


@pytest.mark.asyncio
async def test_reset_command_interactive_confirm():
    runner = CliRunner()
    # User responds "y" to confirmation prompt
    result = runner.invoke(cli, ["reset"], input="y\n")
    assert result.exit_code == 0
    assert "AK5 Kanban data successfully reset to initial state!" in result.output


def test_reset_command_refuses_non_tty_without_yes(monkeypatch):
    # Simulate a background non-interactive agent call
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    runner = CliRunner()
    result = runner.invoke(cli, ["reset"])
    assert result.exit_code == 1
    assert "requires an interactive terminal (TTY)" in result.output


def test_reset_nontty_yes_refused_without_allow_env(monkeypatch):
    """``--yes`` alone is not enough outside a TTY (agents/CI must opt in)."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("AK5_ALLOW_DESTRUCTIVE_RESET", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    runner = CliRunner()
    result = runner.invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 1
    assert "Non-interactive reset refused" in result.output


def test_reset_nontty_yes_allowed_with_env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AK5_ALLOW_DESTRUCTIVE_RESET", "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "ak5.cli.commands.reset.reset_kanban_data",
        lambda **_kwargs: _async_counts(),
    )
    # Avoid dual-DB / wipe surprises in the isolated temp store.
    monkeypatch.setattr(
        "ak5.services.db_safety.prepare_database_for_use",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "ak5.services.db_safety.snapshot_live_database",
        lambda *_a, **_k: None,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 0, result.output
    assert "successfully reset" in result.output
    assert "Auto-restore suppressed" in result.output


async def _async_counts():
    return {
        "tickets": 0,
        "comments": 0,
        "subscriptions": 0,
        "custom_actors": 0,
        "boards": 1,
    }


def test_reset_refuses_dual_db_conflict(tmp_path, monkeypatch):
    import sqlite3

    import ak5.paths as paths
    import ak5.services.db_safety as db_safety

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AK5_ALLOW_DESTRUCTIVE_RESET", "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(db_safety, "_prepared", False)
    monkeypatch.setattr(paths, "_resolved_local_db", False)

    cwd = tmp_path / "project"
    cwd.mkdir()
    app_data = tmp_path / "app"
    monkeypatch.setenv("AK5_DATA_DIR", str(app_data))
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{(app_data / 'ak5.db').as_posix()}",
    )
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(paths.Path, "cwd", lambda: cwd)

    def _write(path, boards, tickets):
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE boards (board_id TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE tickets (ticket_id TEXT PRIMARY KEY)")
            for i in range(boards):
                conn.execute("INSERT INTO boards(board_id) VALUES (?)", (f"b{i}",))
            for i in range(tickets):
                conn.execute("INSERT INTO tickets(ticket_id) VALUES (?)", (f"t{i}",))
            conn.commit()
        finally:
            conn.close()

    _write(cwd / "ak5.db", 5, 38)
    _write(app_data / "ak5.db", 1, 0)

    runner = CliRunner()
    result = runner.invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 1, result.output
    assert "Database conflict" in result.output or "Conflicting AK5 databases" in result.output


@pytest.mark.asyncio
async def test_reset_writes_skip_marker_and_snapshot_message():
    from ak5.config import settings
    from ak5.models.ticket import Ticket
    from ak5.seed import seed_initial_data
    from ak5.services.db_safety import skip_auto_restore_marker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_initial_data()

    todo_col = f"{settings.DEFAULT_BOARD_ID}_todo"
    async with AsyncSessionLocal() as db:
        db.add(
            Ticket(
                ticket_id="TK-RESET-SNAP",
                board_id=settings.DEFAULT_BOARD_ID,
                column_id=todo_col,
                title="Snap me",
                priority="medium",
                rank="a",
                labels="[]",
                created_by="user_pm",
                status="open",
            )
        )
        await db.commit()

    marker = skip_auto_restore_marker()
    if marker.exists():
        marker.unlink()

    runner = CliRunner()
    result = runner.invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Pre-reset snapshot:" in result.output
    assert "Auto-restore suppressed" in result.output
    assert skip_auto_restore_marker().is_file()


@pytest.mark.asyncio
async def test_reset_clears_identity_directory(tmp_path, monkeypatch):
    from ak5.cli.config import find_project_root, identity_dir, write_identity_claim
    from ak5.seed import reset_kanban_data

    root = find_project_root()
    # Write a test identity claim
    claim_path = write_identity_claim(
        actor_id="custom_test_agent",
        role="Worker",
        actor_type="agent",
        project_root=root,
    )
    assert claim_path.exists()
    assert (identity_dir(root) / "custom_test_agent.json").exists()

    # Perform reset with clear_sessions=True
    await reset_kanban_data(clear_sessions=True)

    # Verify identity claim file is removed
    assert not claim_path.exists()
    assert not list(identity_dir(root).glob("*.json"))
