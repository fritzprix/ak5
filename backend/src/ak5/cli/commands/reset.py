import asyncio
import concurrent.futures
import os
import sys
from typing import Any, Coroutine

import click
from rich.console import Console
from rich.panel import Panel

from ak5.seed import reset_kanban_data

console = Console()


def _run_coroutine(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine safely whether or not an event loop is already running."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


@click.command("reset")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Bypass interactive confirmation prompt (scripts/CI only).",
)
@click.option(
    "--keep-boards",
    is_flag=True,
    help="Keep boards and columns; only purge tickets, comments, subscriptions, and custom identities.",
)
@click.option(
    "--clear-sessions/--no-clear-sessions",
    default=True,
    help="Clear local project .ak5 session and identity claim files (default: true).",
)
def reset_command(yes: bool, keep_boards: bool, clear_sessions: bool) -> None:
    """Reset AK5 Kanban state to fresh default seed (interactive confirmation required).

    \b
    [Warning]
      This command permanently removes:
      • All tickets, attachments, comments, and audit logs
      • All registered subscription hooks
      • Custom created identities/agents (restores default PM & core agents)
      • Non-default boards & columns (unless --keep-boards is specified)
      • Local project .ak5 sessions (unless --no-clear-sessions is specified)

    \b
    [Agents / Non-Interactive Safety Guard]
      Automated AI agents running in non-TTY environments cannot execute this command
      without explicit human interaction.
    """
    # 1. Non-interactive guard: require TTY if --yes not provided (protect against background agents)
    is_test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if not yes and not is_test and not sys.stdin.isatty():
        console.print(
            "[bold red]✗ Error: 'ak5 reset' requires an interactive terminal (TTY).[/bold red]\n"
            "[yellow]Automated agents cannot run reset without interactive confirmation.[/yellow]"
        )
        raise SystemExit(1)

    # 2. Interactive confirmation prompt
    if not yes:
        console.print(
            Panel(
                "[bold red]⚠️  DESTRUCTIVE ACTION: RESET KANBAN STATE[/bold red]\n\n"
                "This will permanently delete all tickets, comments, subscriptions, and\n"
                "temporary test identities. The default PM (@user_pm) and default board\n"
                "will be restored to initial state.",
                title="⚠️  Confirmation Required",
                expand=False,
            )
        )
        if not click.confirm("Are you sure you want to reset AK5 Kanban data?", default=False):
            console.print("[yellow]Reset cancelled.[/yellow]")
            return

    console.print("[cyan]▶ Resetting AK5 Kanban data...[/cyan]")
    try:
        counts = _run_coroutine(reset_kanban_data(keep_boards=keep_boards, clear_sessions=clear_sessions))
    except Exception as e:
        console.print(f"[bold red]✗ Reset failed:[/bold red] {e}")
        raise SystemExit(1) from e

    console.print(
        Panel(
            f"[bold green]✓ AK5 Kanban data successfully reset to initial state![/bold green]\n\n"
            f"• Purged tickets: [cyan]{counts['tickets']}[/cyan]\n"
            f"• Purged comments: [cyan]{counts['comments']}[/cyan]\n"
            f"• Purged subscriptions: [cyan]{counts['subscriptions']}[/cyan]\n"
            f"• Removed custom identities: [cyan]{counts['custom_actors']}[/cyan]\n"
            f"• Reset boards: [cyan]{counts['boards']}[/cyan]\n\n"
            "[bold]Active default identities:[/bold]\n"
            "  • [green]@user_pm[/green] (David - Lead PM)\n"
            "  • [green]@agent_image_worker[/green] (Image Worker)\n"
            "  • [green]@agent_code_reviewer[/green] (Code Reviewer)\n\n"
            "[bold]Active default board:[/bold] [cyan]proj-core-engine[/cyan]",
            title="✓ Reset Complete",
            expand=False,
        )
    )
