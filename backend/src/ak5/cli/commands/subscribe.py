import asyncio
import json
import time
from typing import Any

import click
import httpx
from ak5.cli.config import get_api_url, require_auth_headers
from ak5.services.event_context import (
    build_hook_environ,
    extract_event_context,
    matches_agent_filter,
    matches_event_filter,
    normalize_actor_id,
    resolve_hook_session_actor,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# Back-compat alias for tests/callers monkeypatching get_auth_headers
get_auth_headers = require_auth_headers

_LEGACY_PLACEHOLDER_HINTS = (
    "{event}",
    "{event_type}",
    "{board_id}",
    "{ticket_id}",
    "{title}",
    "{actor_id}",
    "{status}",
    "{summary}",
    "{data_json}",
)

# Re-export for tests / callers
__all__ = [
    "extract_event_context",
    "execute_subscriber_command",
    "run_subscription_loop",
    "do_subscribe",
    "do_list_subscriptions",
    "do_remove_subscription",
    "subscribe_group",
]


def _warn_legacy_placeholders(command: str) -> None:
    """Warn if the hook still looks like a curly-brace template (no longer expanded)."""
    hits = [token for token in _LEGACY_PLACEHOLDER_HINTS if token in command]
    if not hits:
        return
    console.print(
        "[bold yellow]⚠ Placeholder tokens are not expanded.[/bold yellow] "
        f"Found {', '.join(hits)}. Use env vars ($AK5_EVENT, $AK5_TICKET_ID, …) or stdin JSON instead."
    )


async def execute_subscriber_command(
    command_str: str,
    context: dict[str, str],
    raw_event: dict[str, Any],
    pass_stdin: bool = True,
    *,
    session_actor_id: str | None = None,
) -> int:
    """Run the hook command literally; event fields are injected via env and optional stdin JSON."""
    env = build_hook_environ(context, session_actor_id=session_actor_id)
    stdin_bytes = json.dumps(raw_event, ensure_ascii=False).encode("utf-8") if pass_stdin else None

    proc = await asyncio.create_subprocess_shell(
        command_str,
        stdin=asyncio.subprocess.PIPE if pass_stdin else None,
        stdout=None,
        stderr=None,
        env=env,
    )

    if pass_stdin and stdin_bytes:
        await proc.communicate(input=stdin_bytes)
    else:
        await proc.wait()

    return proc.returncode or 0


async def run_subscription_loop(
    api_url: str,
    target_board_id: str | None,
    exec_command: str,
    event_filter: set[str] | None = None,
    for_agent: str | None = None,
    ignore_actor: str | None = None,
    debounce_seconds: float = 0.0,
    pass_stdin: bool = True,
    dry_run: bool = False,
    run_once: bool = False,
) -> None:
    """Stream SSE events from gateway and run the hook command on match (Interactive Watch mode)."""
    last_exec: dict[str, float] = {}
    stream_url = f"{api_url}/events/stream"

    board_display = target_board_id or "all boards"
    console.print(
        Panel(
            f"[bold green]Watching events for:[/bold green] [cyan]{board_display}[/cyan]\n"
            f"[bold]Hook Command:[/bold] [yellow]{exec_command}[/yellow]\n"
            f"[dim]Streaming from {stream_url} | Press Ctrl+C to stop[/dim]",
            title="⚡ AK5 Event Watcher",
            expand=False,
        )
    )
    _warn_legacy_placeholders(exec_command)

    headers = get_auth_headers(api_url)
    async with (
        httpx.AsyncClient(timeout=None) as client,
        client.stream("GET", stream_url, headers=headers) as stream,
    ):
        current_event_type: str | None = None

        async for line in stream.aiter_lines():
            line = line.strip()
            if not line:
                current_event_type = None
                continue

            if line.startswith("event:"):
                current_event_type = line.split(":", 1)[1].strip()
                continue

            if line.startswith("data:") and current_event_type:
                raw_data_str = line.split(":", 1)[1].strip()
                try:
                    data = json.loads(raw_data_str)
                except json.JSONDecodeError:
                    continue

                event_type = current_event_type

                if event_type.upper() == "CONNECTED" and (not event_filter or "CONNECTED" not in event_filter):
                    continue

                context = extract_event_context(event_type, data)

                if target_board_id and target_board_id not in ("*", "all"):
                    event_board = context["board_id"]
                    if not event_board or event_board != target_board_id:
                        continue

                if event_filter and not matches_event_filter(event_filter, event_type):
                    continue

                if ignore_actor:
                    actor_id = context.get("actor_id") or ""
                    if normalize_actor_id(actor_id) == normalize_actor_id(ignore_actor):
                        continue

                if for_agent:
                    ticket_data = data.get("ticket") or data.get("subtask")
                    if not matches_agent_filter(for_agent, ticket_data, data):
                        continue

                debounce_key = f"{context['ticket_id']}:{event_type}"
                now = time.time()
                if (
                    debounce_seconds > 0
                    and debounce_key in last_exec
                    and now - last_exec[debounce_key] < debounce_seconds
                ):
                    continue
                last_exec[debounce_key] = now

                timestamp_str = time.strftime("%H:%M:%S")

                console.print(
                    f"[dim][{timestamp_str}][/dim] [bold cyan]⚡ {event_type}[/bold cyan] "
                    f"([green]{context['summary']}[/green])"
                )
                console.print(f"  [dim]➔ Executing:[/dim] [yellow]{exec_command}[/yellow]")

                if dry_run:
                    console.print("  [blue][dry-run] Command not executed.[/blue]")
                else:
                    try:
                        code = await execute_subscriber_command(
                            exec_command,
                            context,
                            {"event": event_type, "data": data},
                            pass_stdin=pass_stdin,
                            session_actor_id=resolve_hook_session_actor(for_agent=for_agent),
                        )
                        if code == 0:
                            console.print("  [bold green]✓ Command completed successfully (code 0)[/bold green]")
                        else:
                            console.print(f"  [bold red]✗ Command exited with non-zero code {code}[/bold red]")
                    except Exception as err:
                        console.print(f"  [bold red]✗ Execution error:[/bold red] {err}")

                if run_once:
                    console.print("[dim]Watch complete (--once specified). Exiting.[/dim]")
                    return


def do_subscribe(
    board_id: str | None,
    exec_command: str,
    events: str | None,
    for_agent: str | None,
    ignore_actor: str | None,
    debounce: float,
    custom_id: str | None,
    dry_run: bool,
) -> None:
    """Core logic to register a subscription hook with the AK5 Gateway."""
    api_url = get_api_url()

    # Dry-run validation: literal command + env/stdin contract (no template expansion)
    if dry_run:
        _warn_legacy_placeholders(exec_command)
        console.print(
            Panel(
                f"[bold green]Subscription Dry-Run Validated[/bold green]\n"
                f"[bold]Board:[/bold] {board_id or '*'}\n"
                f"[bold]Events:[/bold] {events or 'ALL'}\n"
                f"[bold]Hook Command (literal):[/bold] [yellow]{exec_command}[/yellow]\n"
                f"[bold]Injected Env:[/bold] $AK5_EVENT $AK5_TICKET_ID $AK5_TITLE $AK5_SUMMARY "
                f"$AK5_BOARD_ID $AK5_ACTOR_ID $AK5_EVENT_ACTOR_ID $AK5_STATUS $AK5_DATA_JSON\n"
                f"[bold]stdin:[/bold] JSON event payload\n"
                f"[dim]Dry-run mode: Subscription was not registered.[/dim]",
                title="⚡ AK5 Subscribe Dry-Run",
                expand=False,
            )
        )
        return

    payload: dict[str, Any] = {
        "board_id": board_id,
        "exec_command": exec_command,
        "events": [e.strip().upper() for e in events.split(",") if e.strip()] if events else None,
        "for_agent": for_agent,
        "ignore_actor": ignore_actor,
        "debounce_seconds": debounce,
    }
    if custom_id:
        payload["subscription_id"] = custom_id

    _warn_legacy_placeholders(exec_command)

    try:
        headers = get_auth_headers(api_url)
        resp = httpx.post(f"{api_url}/subscriptions", json=payload, headers=headers, timeout=10.0)
        if resp.status_code == 201:
            data = resp.json()
            sub_id = data.get("subscription_id")
            console.print(
                Panel(
                    f"[bold green]✓ Subscription registered successfully![/bold green]\n"
                    f"[bold]ID:[/bold] [cyan]{sub_id}[/cyan]\n"
                    f"[bold]Board:[/bold] {board_id or '*'}\n"
                    f"[bold]Events:[/bold] {events or 'ALL'}\n"
                    f"[bold]Command:[/bold] [yellow]{exec_command}[/yellow]\n"
                    f"[dim]Hook runs on the AK5 Gateway — no local watcher process.[/dim]\n"
                    f"[bold]Verify:[/bold] [cyan]ak5 subscribe ls[/cyan]\n"
                    "[yellow]Gateway runs --exec; its stdout is not your chat — "
                    "--exec must wake your harness.[/yellow]",
                    title="⚡ AK5 Subscription Active",
                    expand=False,
                )
            )
        else:
            console.print(f"[bold red]✗ Failed to register subscription ({resp.status_code}):[/bold red] {resp.text}")
            raise SystemExit(1)
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[bold red]✗ Subscription error:[/bold red] {e}")
        raise SystemExit(1)


def do_list_subscriptions(board_id: str | None) -> None:
    """List all registered subscription hooks from AK5 Gateway."""
    api_url = get_api_url()
    try:
        headers = get_auth_headers(api_url)
        params = {"board_id": board_id} if board_id else {}
        resp = httpx.get(f"{api_url}/subscriptions", params=params, headers=headers, timeout=10.0)
        if resp.status_code != 200:
            console.print(f"[bold red]✗ Failed to fetch subscriptions:[/bold red] {resp.text}")
            raise SystemExit(1)

        subs = resp.json()
        if not subs:
            console.print("[dim]No active subscriptions found.[/dim]")
            return

        table = Table(title="📋 Registered Event Subscriptions", show_header=True, header_style="bold magenta")
        table.add_column("ID", style="cyan", width=16)
        table.add_column("Board", style="green", width=20)
        table.add_column("Events", style="blue", width=20)
        table.add_column("Agent", width=14)
        table.add_column("Hook Command", style="yellow")

        for s in subs:
            evs = ",".join(s.get("events") or []) or "*"
            table.add_row(
                s["subscription_id"],
                s.get("board_id") or "* (all)",
                evs,
                s.get("for_agent") or "-",
                s["exec_command"],
            )
        console.print(table)
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
        raise SystemExit(1)


def do_remove_subscription(subscription_id: str) -> None:
    """Remove a subscription hook from AK5 Gateway."""
    api_url = get_api_url()
    try:
        headers = get_auth_headers(api_url)
        resp = httpx.delete(f"{api_url}/subscriptions/{subscription_id}", headers=headers, timeout=10.0)
        if resp.status_code == 200:
            console.print(
                f"[bold green]✓ Successfully removed subscription hook:[/bold green] [cyan]{subscription_id}[/cyan]"
            )
        elif resp.status_code == 404:
            console.print(f"[bold yellow]✗ Subscription '{subscription_id}' not found.[/bold yellow]")
            raise SystemExit(1)
        else:
            console.print(f"[bold red]✗ Failed to remove subscription ({resp.status_code}):[/bold red] {resp.text}")
            raise SystemExit(1)
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
        raise SystemExit(1)


@click.group("subscribe")
def subscribe_group() -> None:
    """Manage server-side board event hooks (Register & Return).

    \b
    [Agent Best Practices & Event Subscription Guide]
      1. DO NOT specify --events unless strictly necessary!
         - Omit --events (default: ALL / core lifecycle):
           Automatically receives all core events:
           • TICKET_CREATED   (new ticket assigned to agent)
           • TICKET_DELEGATED (subtask delegated to agent)
           • TICKET_UPDATED   (assignee changed, priority changed, etc.)
           • TICKET_MOVED     (ticket moved between columns)
           • COMMENT_ADDED    (new comment or discussion mention)
         - Explicitly listing --events is an overhead and common cause of dropped tasks
           (e.g. omitting DELEGATED drops subtasks, omitting UPDATED drops re-assignments).
         - If specified, supports 'DEFAULT', 'TICKET' (all ticket events), and aliases (e.g. TICKET_COMMENTED).

    \b
      2. --for-agent <AGENT_ID>
         - Safely accepts actor ID with or without '@' (e.g. "my-agent" or "@my-agent").
         - Filters events to tickets/subtasks assigned to your agent.

    \b
      3. Register & Return pattern:
         - `ak5 subscribe create` registers a persistent server-side hook and exits immediately (exit 0).
         - Verify registration with: `ak5 subscribe ls`.
         - Do NOT run background polling loops or local SSE processes.

    \b
    [How hooks work]
      - create registers a literal --exec command and exits immediately (exit 0).
      - On matching board events, the AK5 Gateway runs that command as-is.
      - Event data is NOT interpolated into --exec. Use env vars / stdin instead.
      - Each event spawn gets a fresh process env for that event (runtime per invocation).
      - Do NOT use curly-brace tokens like {ticket_id} (they are not expanded).
      - Gateway --exec stdout is not your chat; --exec must wake the harness.

    \b
    [Event payload]
      Env:  $AK5_EVENT  $AK5_EVENT_TYPE  $AK5_BOARD_ID  $AK5_TICKET_ID
            $AK5_TITLE  $AK5_ACTOR_ID  $AK5_EVENT_ACTOR_ID  $AK5_STATUS
            $AK5_SUMMARY  $AK5_DATA_JSON
      $AK5_ACTOR_ID binds the woken session (--for-agent / subscription owner).
      $AK5_EVENT_ACTOR_ID is who caused the event.
      stdin: JSON {"event": "...", "data": {...}}  (e.g. curl -d @-)

    \b
    [Examples]
      # Recommended for Agents (no --events needed; auto-subscribes all core lifecycle):
      ak5 subscribe create proj-core-engine --for-agent "$AK5_ACTOR_ID" \
        --exec 'agent -p "$AK5_SUMMARY"'

    \b
      # Webhook forwarding (all events):
      ak5 subscribe create proj-core-engine \
        --exec 'curl -X POST https://example.com/hook -H "Content-Type: application/json" -d @-'

    \b
      # Inspect & remove:
      ak5 subscribe list
      ak5 subscribe remove <ID>

    \b
    [Human terminal SSE]
      ak5 events watch <BOARD_ID> --exec '…'   (not for agents)
    """
    pass


@subscribe_group.command("add")
@click.argument("board_id", required=False, default=None, metavar="[BOARD_ID]")
@click.option(
    "--exec",
    "-x",
    "exec_command",
    required=True,
    help=(
        "Literal shell command that WAKES your agent runtime on match (required). "
        "Pass context via $AK5_EVENT / $AK5_TICKET_ID / $AK5_SUMMARY / stdin JSON. "
        "Do not put claim/move/work here (those run in the woken session). "
        "No {placeholder} expansion."
    ),
)
@click.option(
    "--events",
    "-e",
    default=None,
    help=(
        "Optional event filter (comma-separated, e.g. TICKET, or TICKET_CREATED,TICKET_MOVED). "
        "RECOMMENDED FOR AGENTS: Omit this option to automatically subscribe to all core lifecycle "
        "events (TICKET_CREATED, TICKET_DELEGATED, TICKET_UPDATED, TICKET_MOVED, COMMENT_ADDED). "
        "Supports 'DEFAULT', 'TICKET' (all ticket events), and aliases."
    ),
)
@click.option(
    "--for-agent",
    "-a",
    default=None,
    help="Only tickets assigned to this actor ID (with or without leading '@').",
)
@click.option(
    "--ignore-actor",
    default=None,
    help="Skip events whose actor_id matches this ID (with or without leading '@').",
)
@click.option("--debounce", default=0.0, type=float, help="Per-ticket cooldown seconds between runs.")
@click.option("--id", "custom_id", default=None, help="Custom subscription ID (default: auto-generated).")
@click.option("--dry-run", is_flag=True, help="Print hook contract and exit without registering.")
def subscribe_add(
    board_id: str | None,
    exec_command: str,
    events: str | None,
    for_agent: str | None,
    ignore_actor: str | None,
    debounce: float,
    custom_id: str | None,
    dry_run: bool,
) -> None:
    """Register a server-side hook and return immediately.

    \b
    Examples:
      # Recommended: omit --events so all core events (created, delegated, updated, moved, comments) are captured
      ak5 subscribe create proj-core-engine --for-agent "$AK5_ACTOR_ID" \
        --exec 'agent -p "$AK5_SUMMARY"'
      ak5 subscribe create proj-core-engine --exec './scripts/on_board_event.sh'
      ak5 subscribe create --exec 'curl -X POST https://hooks.example/ak5 -d @-' --dry-run
    """
    do_subscribe(
        board_id=board_id,
        exec_command=exec_command,
        events=events,
        for_agent=for_agent,
        ignore_actor=ignore_actor,
        debounce=debounce,
        custom_id=custom_id,
        dry_run=dry_run,
    )


@subscribe_group.command("list")
@click.option("--board", "-b", "board_id", default=None, help="Filter by target board ID.")
def subscribe_list(board_id: str | None) -> None:
    """List hooks registered by the current actor (admins see all)."""
    do_list_subscriptions(board_id)


@subscribe_group.command("remove")
@click.argument("subscription_id", required=True, metavar="<SUBSCRIPTION_ID>")
def subscribe_remove(subscription_id: str) -> None:
    """Delete a registered hook by ID (owner or admin only)."""
    do_remove_subscription(subscription_id)


@subscribe_group.command("watch")
@click.argument("board_id", required=False, default=None, metavar="[BOARD_ID]")
@click.option(
    "--exec",
    "-x",
    "exec_command",
    required=False,
    default=None,
    help="Deprecated path. Prefer: ak5 events watch --exec '…'",
)
@click.option("--demo-echo", is_flag=True, help="Deprecated path. Prefer: ak5 events watch --demo-echo")
@click.option("--events", "-e", default=None, help="Comma-separated event types. Default: all.")
@click.option("--for-agent", "-a", default=None, help="Only tickets assigned to this actor ID.")
@click.option("--debounce", default=0.0, type=float, help="Per-ticket cooldown seconds between runs.")
@click.option("--once", is_flag=True, help="Exit after the first matching event.")
@click.pass_context
def subscribe_watch(
    ctx: click.Context,
    board_id: str | None,
    exec_command: str | None,
    demo_echo: bool,
    events: str | None,
    for_agent: str | None,
    debounce: float,
    once: bool,
) -> None:
    """DEPRECATED. Agents: subscribe create. Humans: ak5 events watch."""
    from ak5.cli.agent_env import refuse_blocking_watch_for_agents
    from ak5.cli.commands.events import events_watch

    console.print(
        "[bold yellow]DEPRECATED:[/bold yellow] "
        "[cyan]ak5 subscribe watch[/cyan] is not agent subscription.\n"
        "  Agents: [cyan]ak5 subscribe create <BOARD> --exec '…'[/cyan] then [cyan]ak5 subscribe ls[/cyan]\n"
        "  Humans: [cyan]ak5 events watch <BOARD> --exec '…'[/cyan]"
    )
    refuse_blocking_watch_for_agents()
    ctx.invoke(
        events_watch,
        board_id=board_id,
        exec_command=exec_command,
        demo_echo=demo_echo,
        events=events,
        for_agent=for_agent,
        debounce=debounce,
        once=once,
    )


# Subcommand aliases
subscribe_group.add_command(subscribe_add, name="create")
subscribe_group.add_command(subscribe_list, name="ls")
subscribe_group.add_command(subscribe_remove, name="rm")

# Aliases for convenience / backward-compatibility
subscribe_command = subscribe_group
subscriptions_command = subscribe_list
unsubscribe_command = subscribe_remove

