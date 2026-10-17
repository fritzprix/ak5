"""Delegate as create_direct with parent (cli.md §5 — pull model, no --to)."""

from __future__ import annotations

import click
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from rich.console import Console

console = Console()


@click.command("delegate")
@click.argument("ticket_id")
@click.option("--title", required=True, help="Subtask title")
@click.option("--desc", "description", default="", help="Subtask detailed description / spec")
@click.option("--spec", default=None, help="Explicit spec (defaults to --desc / --title)")
@click.option(
    "--criteria",
    "criteria_raw",
    default=None,
    help="Comma-separated acceptance criteria",
)
@click.option("--board", default=None, help="Target board (default: parent's board)")
@click.option("--priority", type=click.Choice(["low", "medium", "high", "urgent"]), default="medium")
@click.option(
    "--to",
    "target_actor_id",
    default=None,
    hidden=True,
    help="Deprecated (pull model); ignored if provided.",
)
@click.pass_context
def delegate_command(
    ctx: click.Context,
    ticket_id: str,
    title: str,
    description: str,
    spec: str | None,
    criteria_raw: str | None,
    board: str | None,
    priority: str,
    target_actor_id: str | None,
) -> None:
    """Create a child ticket on a board backlog (create_direct / pull model).

    ``--to`` is removed from the happy path: work is claimed via ``ak5 next`` /
    ``ak5 ticket claim``, not push-assigned.
    """
    _ = target_actor_id
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    criteria = (
        [c.strip() for c in criteria_raw.split(",") if c.strip()]
        if criteria_raw
        else None
    )
    payload: dict = {
        "subtask_title": title,
        "subtask_description": description or None,
        "spec": spec or description or title,
        "priority": priority,
        "labels": ["delegated", "cli"],
    }
    if criteria:
        payload["acceptance_criteria"] = criteria
    if board:
        payload["board_id"] = board

    try:
        with open_client() as client:
            subtask = client.delegate(ticket_id, payload)
        if json_mode:
            print_result(subtask, json_mode=True)
        else:
            console.print(
                f"[bold green]✓[/bold green] Subtask [cyan]{subtask['ticket_id']}[/cyan] "
                f"created on backlog (parent: [yellow]{ticket_id}[/yellow])"
            )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)
