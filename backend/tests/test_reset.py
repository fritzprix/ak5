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
