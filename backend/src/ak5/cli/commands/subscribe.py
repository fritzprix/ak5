import asyncio
import json
import time
from typing import Any

import click
import httpx
from ak5.cli.config import clean_actor_id, get_api_url, require_auth_headers
from ak5.contracts.event_context import (
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
        "for_agent": clean_actor_id(for_agent) if for_agent else None,
        "ignore_actor": clean_actor_id(ignore_actor) if ignore_actor else None,
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
    """Manage local daemon subscribers that wake your agent (Register & Return).

    \b
    Subscribers live in the client daemon, not on the server (client.md §4.3).
    The daemon keeps one server subscription per profile and fans events out
    to every local subscriber. `subscribe add` stores the hook and exits 0.

    \b
    [Agents]
      - Usually filter on WORK_AVAILABLE (your role has claimable work).
      - --exec must WAKE your harness; claim / work / submit run in the woken
        session, never inside --exec.
      - Verify with `ak5 subscribe ls`. Do not run background poll loops;
        to block until work exists use `ak5 next --wait 25` instead.

    \b
    [How hooks work]
      - The daemon runs --exec literally in a shell for each matching event.
      - Event data is NOT interpolated into --exec. Use env vars / stdin.
      - Do NOT use curly-brace tokens like {ticket_id} (they are not expanded).
      - The hook does not inherit your profile: set it inside --exec
        (e.g. --exec 'AK5_PROFILE=worker-1 claude -p "$AK5_SUMMARY"').

    \b
    [Event payload]
      Env:  $AK5_EVENT  $AK5_EVENT_TYPE  $AK5_EVENT_ID  $AK5_CURSOR
            $AK5_BOARD_ID  $AK5_TICKET_ID  $AK5_TITLE  $AK5_STATUS
            $AK5_SUMMARY  $AK5_ACTOR_ID  $AK5_EVENT_ACTOR_ID  $AK5_DATA_JSON
      $AK5_ACTOR_ID / $AK5_EVENT_ACTOR_ID: who caused the event.
      stdin: event JSON (e.g. curl -d @-)

    \b
    [Examples]
      ak5 subscribe add proj-core-engine --events WORK_AVAILABLE \\
        --exec 'AK5_PROFILE=worker-1 claude -p "$AK5_SUMMARY"'
      ak5 subscribe add --events HUMAN_ACTION_REQUIRED \\
        --exec 'notify-send "AK5" "$AK5_SUMMARY"'
      ak5 subscribe add --preset curl:http://127.0.0.1:3030/hook
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
    default=None,
    help=(
        "Literal shell command that WAKES your agent runtime on match "
        "(required unless --preset). "
        "Pass context via $AK5_EVENT / $AK5_TICKET_ID / $AK5_SUMMARY / stdin JSON. "
        "Do not put claim/move/work here (those run in the woken session). "
        "No {placeholder} expansion."
    ),
)
@click.option(
    "--events",
    "-e",
    default=None,
    help="Optional event filter (comma-separated). Omit for all events the daemon spools.",
)
@click.option("--id", "custom_id", default=None, help="Custom subscriber ID (default: auto-generated).")
@click.option("--dry-run", is_flag=True, help="Print hook contract and exit without registering.")
@click.option("--profile", default=None, help="Profile (default: current AK5_PROFILE / profiles.toml default)")
@click.option(
    "--preset",
    default=None,
    help="Build --exec from a preset (claude|cursor|curl:<url>); still stored as exec.",
)
def subscribe_add(
    board_id: str | None,
    exec_command: str | None,
    events: str | None,
    custom_id: str | None,
    dry_run: bool,
    profile: str | None,
    preset: str | None,
) -> None:
    """Register a **local** daemon subscriber (client.md §4.3). Never creates a server hook.

    \b
    Examples:
      ak5 subscribe add --exec 'agent -p \"$AK5_SUMMARY\"' --events WORK_AVAILABLE
      ak5 subscribe add proj-core-engine --preset claude --events WORK_AVAILABLE
    """
    if preset:
        if preset == "claude":
            exec_command = 'claude -p "$AK5_SUMMARY"'
        elif preset == "cursor":
            exec_command = 'agent -p "$AK5_SUMMARY"'
        elif preset.startswith("curl:"):
            url = preset[5:]
            exec_command = (
                f'curl -s -X POST {url} -H "Content-Type: application/json" -d @-'
            )
        else:
            raise click.UsageError(f"unknown preset: {preset}")
    if not exec_command:
        raise click.UsageError("Provide --exec '<command>' or --preset claude|cursor|curl:<url>.")

    _warn_legacy_placeholders(exec_command)

    if dry_run:
        console.print(
            Panel.fit(
                f"local=true\nexec={exec_command}\nevents={events}\nboard={board_id}\nprofile={profile}",
                title="subscribe dry-run",
            )
        )
        return

    from ak5.daemon.ipc import daemon_client, ensure_daemon

    ensure_daemon()
    event_list = [e.strip() for e in (events or "").split(",") if e.strip()]
    with daemon_client() as client:
        resp = client.post(
            "/v1/subscribers",
            json={
                "profile": profile,
                "board_id": board_id,
                "events": event_list,
                "exec_command": exec_command,
                "id": custom_id,
            },
        )
        resp.raise_for_status()
        body = resp.json()
    console.print(
        f"[green]✓ local subscriber[/green] {body.get('id')} "
        f"(profile={body.get('profile')})"
    )


@subscribe_group.command("list")
@click.option("--board", "-b", "board_id", default=None, help="Filter by target board ID.")
@click.option("--profile", default=None, help="Profile for local subscribers")
@click.option(
    "--server",
    "list_server",
    is_flag=True,
    help="List legacy server subscriptions (usually empty after #11).",
)
def subscribe_list(board_id: str | None, profile: str | None, list_server: bool) -> None:
    """List local daemon subscribers (default)."""
    if list_server:
        do_list_subscriptions(board_id)
        return
    from ak5.daemon.ipc import daemon_client, ensure_daemon

    ensure_daemon()
    with daemon_client() as client:
        resp = client.get("/v1/subscribers", params={"profile": profile} if profile else None)
        resp.raise_for_status()
        rows = resp.json()
    if board_id:
        rows = [r for r in rows if r.get("board_id") in (board_id, None, "*")]
    if not rows:
        console.print("[dim]No local subscribers.[/dim]")
        return
    table = Table(title="Local subscribers", show_header=True, header_style="bold magenta")
    table.add_column("ID", style="cyan")
    table.add_column("Profile")
    table.add_column("Board")
    table.add_column("Events")
    table.add_column("Exec", style="yellow")
    for s in rows:
        table.add_row(
            s.get("id", ""),
            s.get("profile", ""),
            s.get("board_id") or "*",
            ",".join(s.get("events") or []) or "*",
            s.get("exec_command", ""),
        )
    console.print(table)


@subscribe_group.command("remove")
@click.argument("subscription_id", required=True, metavar="<SUBSCRIPTION_ID>")
@click.option(
    "--server",
    "remove_server",
    is_flag=True,
    help="Remove a legacy server subscription instead of a local one.",
)
def subscribe_remove(subscription_id: str, remove_server: bool) -> None:
    """Delete a local daemon subscriber by ID."""
    if remove_server:
        do_remove_subscription(subscription_id)
        return
    from ak5.daemon.ipc import daemon_client, ensure_daemon

    ensure_daemon()
    with daemon_client() as client:
        resp = client.delete(f"/v1/subscribers/{subscription_id}")
        if resp.status_code == 404:
            console.print(f"[yellow]subscriber not found:[/yellow] {subscription_id}")
            raise SystemExit(1)
        resp.raise_for_status()
    console.print(f"[green]✓ removed[/green] {subscription_id}")


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
    """DEPRECATED. Agents: subscribe add. Humans: ak5 events watch."""
    from ak5.cli.agent_env import refuse_blocking_watch_for_agents
    from ak5.cli.commands.events import events_watch

    console.print(
        "[bold yellow]DEPRECATED:[/bold yellow] "
        "[cyan]ak5 subscribe watch[/cyan] is not agent subscription.\n"
        "  Agents: [cyan]ak5 subscribe add <BOARD> --exec '…'[/cyan] then [cyan]ak5 subscribe ls[/cyan]\n"
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

