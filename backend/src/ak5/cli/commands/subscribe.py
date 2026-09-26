import asyncio
import json
import os
import time
from typing import Any

import click
import httpx
from ak5.cli.config import get_api_url
from rich.console import Console
from rich.panel import Panel

console = Console()


def extract_event_context(event_type: str, data: dict[str, Any], default_board_id: str | None = None) -> dict[str, str]:
    """Extract standard template placeholders and summaries from an event payload."""
    ticket = data.get("ticket") or data.get("subtask") or {}
    ticket_id = (
        ticket.get("ticket_id")
        or data.get("ticket_id")
        or data.get("parent_ticket_id")
        or ""
    )
    board_id = (
        data.get("board_id")
        or ticket.get("board_id")
        or default_board_id
        or ""
    )
    title = ticket.get("title", "")
    actor_id = data.get("actor_id") or data.get("comment", {}).get("actor_id") or ""
    status = ticket.get("status", "")
    column_id = ticket.get("column_id", "")
    from_column = data.get("from_column", "")
    to_column = data.get("to_column", "")

    # Human-readable summary
    if event_type == "TICKET_CREATED":
        summary = f"Ticket {ticket_id} ('{title}') created by @{actor_id}"
    elif event_type == "TICKET_MOVED":
        summary = f"Ticket {ticket_id} moved to {to_column or column_id} by @{actor_id}"
    elif event_type == "TICKET_DELEGATED":
        summary = f"Subtask {ticket_id} ('{title}') delegated by @{actor_id}"
    elif event_type == "TICKET_UPDATED":
        summary = f"Ticket {ticket_id} updated by @{actor_id}"
    elif event_type == "COMMENT_ADDED":
        summary = f"Comment added on {ticket_id} by @{actor_id}"
    elif event_type == "ATTACHMENT_ADDED":
        att = data.get("attachment", {})
        summary = f"Attachment '{att.get('filename')}' uploaded to {ticket_id}"
    elif event_type == "ATTACHMENT_DELETED":
        summary = f"Attachment {data.get('attachment_id')} deleted from {ticket_id}"
    else:
        summary = f"Event {event_type} on {ticket_id or board_id}"

    data_json = json.dumps(data, ensure_ascii=False)

    return {
        "event": event_type,
        "event_type": event_type,
        "board_id": board_id,
        "ticket_id": ticket_id,
        "title": title,
        "actor_id": actor_id,
        "status": status,
        "column_id": column_id,
        "from_column": from_column,
        "to_column": to_column,
        "summary": summary,
        "data_json": data_json,
    }


def render_command_string(template: str, context: dict[str, str]) -> str:
    """Substitute placeholders like {ticket_id} in the command template."""
    rendered = template
    for key, val in context.items():
        placeholder = f"{{{key}}}"
        if placeholder in rendered:
            rendered = rendered.replace(placeholder, val)
    return rendered


async def execute_subscriber_command(
    command_str: str,
    context: dict[str, str],
    raw_event: dict[str, Any],
    pass_stdin: bool = True,
) -> int:
    """Execute the crafted subscriber shell command with environment variables and stdin."""
    env = os.environ.copy()
    env["AK5_EVENT"] = context["event"]
    env["AK5_EVENT_TYPE"] = context["event_type"]
    env["AK5_BOARD_ID"] = context["board_id"]
    env["AK5_TICKET_ID"] = context["ticket_id"]
    env["AK5_TITLE"] = context["title"]
    env["AK5_ACTOR_ID"] = context["actor_id"]
    env["AK5_STATUS"] = context["status"]
    env["AK5_SUMMARY"] = context["summary"]
    env["AK5_DATA_JSON"] = context["data_json"]

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
    exec_template: str,
    event_filter: set[str] | None = None,
    for_agent: str | None = None,
    ignore_actor: str | None = None,
    debounce_seconds: float = 0.0,
    pass_stdin: bool = True,
    dry_run: bool = False,
    run_once: bool = False,
) -> None:
    """Stream SSE events from gateway and execute the subscriber command on match."""
    last_exec: dict[str, float] = {}
    stream_url = f"{api_url}/events/stream"

    board_display = target_board_id or "all boards"
    console.print(
        Panel(
            f"[bold green]Subscribing to events for:[/bold green] [cyan]{board_display}[/cyan]\n"
            f"[bold]Command Template:[/bold] [yellow]{exec_template}[/yellow]\n"
            f"[dim]Streaming from {stream_url} | Press Ctrl+C to stop[/dim]",
            title="⚡ AK5 Event Subscriber",
            expand=False,
        )
    )

    async with (
        httpx.AsyncClient(timeout=None) as client,
        client.stream("GET", stream_url) as stream,
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

                # Skip initial connection ping unless explicitly requested
                if event_type.upper() == "CONNECTED" and (not event_filter or "CONNECTED" not in event_filter):
                    continue

                context = extract_event_context(event_type, data, target_board_id)

                # 1. Board ID filter (if specified and not wildcard)
                if target_board_id and target_board_id not in ("*", "all"):
                    event_board = context["board_id"]
                    if event_board and event_board != target_board_id:
                        continue

                # 2. Event type filter
                if event_filter and event_type.upper() not in event_filter:
                    continue

                # 3. Ignore actor filter (prevent loop)
                if ignore_actor and context["actor_id"] == ignore_actor:
                    continue

                # 4. Target agent filter
                if for_agent:
                    ticket_data = data.get("ticket") or data.get("subtask") or {}
                    assigned = ticket_data.get("assigned_to")
                    if assigned != for_agent:
                        continue

                # 5. Debounce check
                debounce_key = f"{context['ticket_id']}:{event_type}"
                now = time.time()
                if (
                    debounce_seconds > 0
                    and debounce_key in last_exec
                    and now - last_exec[debounce_key] < debounce_seconds
                ):
                    continue
                last_exec[debounce_key] = now

                # 6. Render command
                rendered_cmd = render_command_string(exec_template, context)
                timestamp_str = time.strftime("%H:%M:%S")

                console.print(
                    f"[dim][{timestamp_str}][/dim] [bold cyan]⚡ {event_type}[/bold cyan] "
                    f"([green]{context['summary']}[/green])"
                )
                console.print(f"  [dim]➔ Executing:[/dim] [yellow]{rendered_cmd}[/yellow]")

                if dry_run:
                    console.print("  [blue][dry-run] Command not executed.[/blue]")
                else:
                    try:
                        code = await execute_subscriber_command(
                            rendered_cmd,
                            context,
                            {"event": event_type, "data": data},
                            pass_stdin=pass_stdin,
                        )
                        if code == 0:
                            console.print("  [bold green]✓ Command completed successfully (code 0)[/bold green]")
                        else:
                            console.print(f"  [bold red]✗ Command exited with non-zero code {code}[/bold red]")
                    except Exception as err:
                        console.print(f"  [bold red]✗ Execution error:[/bold red] {err}")

                if run_once:
                    console.print("[dim]Subscription complete (--once specified). Exiting.[/dim]")
                    return


@click.command("subscribe")
@click.argument("board_id", required=False, default=None, metavar="[BOARD_ID]")
@click.option(
    "--exec",
    "-x",
    "exec_command",
    required=True,
    help="Shell command template to execute when an event occurs. Supports placeholders: {event}, {board_id}, {ticket_id}, {title}, {actor_id}, {summary}, {data_json}.",
)
@click.option(
    "--events",
    "-e",
    default=None,
    help="Comma-separated event types to observe (e.g. 'TICKET_MOVED,TICKET_DELEGATED'). Defaults to all.",
)
@click.option(
    "--for-agent",
    "-a",
    default=None,
    help="Filter events only for tickets assigned to this agent ID (e.g. 'cursor-agent').",
)
@click.option(
    "--ignore-actor",
    default=None,
    help="Ignore events initiated by this actor ID to avoid execution loops.",
)
@click.option(
    "--debounce",
    default=0.0,
    type=float,
    help="Minimum seconds cooldown between executing command for the same ticket event.",
)
@click.option(
    "--pass-stdin/--no-stdin",
    default=True,
    help="Pass raw event JSON payload to the command's stdin (default: True).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Display rendered command without executing it.",
)
@click.option(
    "--once",
    is_flag=True,
    help="Exit immediately after handling the first matching event.",
)
def subscribe_command(
    board_id: str | None,
    exec_command: str,
    events: str | None,
    for_agent: str | None,
    ignore_actor: str | None,
    debounce: float,
    pass_stdin: bool,
    dry_run: bool,
    once: bool,
) -> None:
    """Subscribe to real-time board events and execute a crafted shell command.

    Examples:

      # 1. Trigger an agent prompt upon ticket delegation or status move
      ak5 subscribe proj-core-engine --exec 'agent -p "subscription from ak5: {summary}"'

      # 2. Call an external webhook with curl
      ak5 subscribe proj-core-engine --exec 'curl -X POST https://example.com/webhook -H "Content-Type: application/json" -d "$AK5_DATA_JSON"'

      # 3. Log matching events with jq via stdin
      ak5 subscribe proj-core-engine --exec 'jq -c "{event: .event, ticket: .data.ticket.ticket_id}" >> /tmp/ak5.log'
    """
    api_url = get_api_url()

    event_filter = None
    if events:
        event_filter = {ev.strip().upper() for ev in events.split(",") if ev.strip()}

    try:
        asyncio.run(
            run_subscription_loop(
                api_url=api_url,
                target_board_id=board_id,
                exec_template=exec_command,
                event_filter=event_filter,
                for_agent=for_agent,
                ignore_actor=ignore_actor,
                debounce_seconds=debounce,
                pass_stdin=pass_stdin,
                dry_run=dry_run,
                run_once=once,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Subscription stopped by user.[/dim]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]✗ Subscription error:[/bold red] {e}")
