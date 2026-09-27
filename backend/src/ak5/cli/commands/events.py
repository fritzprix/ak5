"""Human-terminal board event observation (blocking SSE). Not for agent hooks."""

from __future__ import annotations

import asyncio

import click
import httpx
from ak5.cli.agent_env import refuse_blocking_watch_for_agents
from ak5.cli.commands.subscribe import run_subscription_loop
from ak5.cli.config import get_api_url
from rich.console import Console

console = Console()

_DEMO_ECHO = 'echo "[$AK5_EVENT] $AK5_TICKET_ID: $AK5_TITLE"'


@click.group("events")
def events_group() -> None:
    """Observe board SSE streams in a human terminal (blocking).

    \b
    Agents must NOT use this group. Register hooks instead:
      ak5 subscribe create <BOARD_ID> --exec '…'
      ak5 subscribe ls
    """


@events_group.command("watch")
@click.argument("board_id", required=False, default=None, metavar="[BOARD_ID]")
@click.option(
    "--exec",
    "-x",
    "exec_command",
    required=False,
    default=None,
    help=(
        "Literal shell command for each matching event (required unless --demo-echo). "
        "Uses env ($AK5_EVENT, …) + stdin JSON; no {placeholder} expansion."
    ),
)
@click.option(
    "--demo-echo",
    is_flag=True,
    help=f"Use demo command: {_DEMO_ECHO}",
)
@click.option(
    "--events",
    "-e",
    default=None,
    help="Comma-separated event types to observe. Default: all.",
)
@click.option("--for-agent", "-a", default=None, help="Only tickets assigned to this actor ID.")
@click.option("--debounce", default=0.0, type=float, help="Per-ticket cooldown seconds between runs.")
@click.option("--once", is_flag=True, help="Exit after the first matching event.")
def events_watch(
    board_id: str | None,
    exec_command: str | None,
    demo_echo: bool,
    events: str | None,
    for_agent: str | None,
    debounce: float,
    once: bool,
) -> None:
    """Foreground SSE watcher for humans (blocking). Agents: use subscribe create.

    \b
    Examples:
      ak5 events watch proj-core-engine --exec 'notify-send \"$AK5_TITLE\"'
      ak5 events watch proj-core-engine --demo-echo --once
    """
    refuse_blocking_watch_for_agents()

    if demo_echo and exec_command:
        raise click.UsageError("Use either --exec or --demo-echo, not both.")
    if demo_echo:
        exec_command = _DEMO_ECHO
    if not exec_command:
        raise click.UsageError(
            "--exec is required for events watch "
            "(or pass --demo-echo for a terminal demo). "
            "Agents should use: ak5 subscribe create <BOARD> --exec '...'"
        )

    api_url = get_api_url()
    event_filter = None
    if events:
        event_filter = {ev.strip().upper() for ev in events.split(",") if ev.strip()}

    try:
        asyncio.run(
            run_subscription_loop(
                api_url=api_url,
                target_board_id=board_id,
                exec_command=exec_command,
                event_filter=event_filter,
                for_agent=for_agent,
                debounce_seconds=debounce,
                run_once=once,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped by user.[/dim]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
        raise SystemExit(1)


# Top-level alias: `ak5 watch` → same as `ak5 events watch`
watch_command = events_watch
events_command = events_group
