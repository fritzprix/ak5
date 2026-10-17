"""ak5 next — peek at next claimable ticket without claiming (cli.md §4.3)."""

from __future__ import annotations

import click
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from rich.console import Console

console = Console()


@click.command("next")
@click.option("--board", "board_id", default=None, help="Board ID (default: inbox if --inbox)")
@click.option("--inbox", is_flag=True, help="Use the system inbox board")
@click.option("--caps", default="", help="Comma-separated capabilities filter")
@click.pass_context
def next_command(
    ctx: click.Context,
    board_id: str | None,
    inbox: bool,
    caps: str,
) -> None:
    """Return the next claimable ticket (does not claim). Exit 6 if none."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    target = "inbox" if inbox or not board_id else board_id
    capabilities = [c.strip() for c in caps.split(",") if c.strip()] or None

    try:
        with open_client() as client:
            ticket = client.next_ticket(target, capabilities=capabilities)
        if json_mode:
            print_result(ticket, json_mode=True)
        else:
            console.print(
                f"[bold green]✓[/bold green] Next: [cyan]{ticket.get('ticket_id')}[/cyan] "
                f"— {ticket.get('title')} (stage={ticket.get('stage')})"
            )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)
