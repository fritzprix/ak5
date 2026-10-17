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


@events_group.command("wait")
@click.option("--profile", default=None, help="Profile name (default: AK5_PROFILE / default_profile)")
@click.option("--events", "-e", default=None, help="Comma-separated event types")
@click.option("--timeout", default=25.0, type=float, help="Seconds to wait (exit 6 on timeout)")
@click.option("--cursor", default=0, type=int, help="Local spool cursor")
@click.pass_context
def events_wait(
    ctx: click.Context,
    profile: str | None,
    events: str | None,
    timeout: float,
    cursor: int,
) -> None:
    """Wait for a local daemon event (client.md §4.4). Exit 6 on timeout."""
    import json

    from ak5.daemon.ipc import daemon_client, ensure_daemon

    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    ensure_daemon()
    params: dict = {"cursor": cursor, "wait": timeout}
    if profile:
        params["profile"] = profile
    if events:
        params["events"] = events
    with daemon_client(timeout=timeout + 5) as client:
        resp = client.get("/v1/events", params=params)
        resp.raise_for_status()
        body = resp.json()
    if json_mode:
        click.echo(json.dumps(body))
    else:
        console.print(body)
    if not body.get("events"):
        raise SystemExit(6)


@events_group.command("poll")
@click.option("--profile", default=None)
@click.option("--events", "-e", default=None)
@click.option("--cursor", default=0, type=int)
@click.pass_context
def events_poll(ctx: click.Context, profile: str | None, events: str | None, cursor: int) -> None:
    """One-shot local spool poll (or server poll if daemon is down — server path TBD)."""
    import json

    from ak5.daemon.ipc import daemon_client, ensure_daemon
    from ak5.daemon.server import is_daemon_running

    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    if not is_daemon_running():
        # Fallback note: without daemon, agents use server GET /events via AK5Client later.
        raise click.ClickException("daemon not running — start with `ak5 daemon start`")
    ensure_daemon()
    params: dict = {"cursor": cursor, "wait": 0}
    if profile:
        params["profile"] = profile
    if events:
        params["events"] = events
    with daemon_client() as client:
        resp = client.get("/v1/events", params=params)
        resp.raise_for_status()
        body = resp.json()
    if json_mode:
        click.echo(json.dumps(body))
    else:
        console.print(body)


# Top-level alias: `ak5 watch` → same as `ak5 events watch`
watch_command = events_watch
events_command = events_group
