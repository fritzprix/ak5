"""Board list + deprecated top-level create-board / claim-board aliases (cli.md §4.5 / §5)."""

from __future__ import annotations

import click
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from rich.console import Console
from rich.table import Table

console = Console()


def render_boards_table(boards: list[dict]) -> Table:
    table = Table(title="Available Kanban Boards", show_lines=True)
    table.add_column("Board ID", style="cyan bold", no_wrap=True)
    table.add_column("Name", style="magenta bold")
    table.add_column("Kind", style="yellow")
    table.add_column("Template", style="dim")
    table.add_column("Description", style="white")
    table.add_column("Created By", style="green")

    for b in boards:
        table.add_row(
            b["board_id"],
            b["name"],
            str(b.get("kind") or "standard"),
            str(b.get("template") or ""),
            b.get("description") or "[dim]N/A[/dim]",
            f"@{b['created_by']}",
        )
    return table


@click.command("boards")
@click.option("--query", "-q", default=None, help="Filter boards by name or description")
@click.pass_context
def boards_command(ctx: click.Context, query: str | None) -> None:
    """List all available Kanban boards."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    try:
        with open_client() as client:
            boards = client.list_boards()

        if query:
            q = query.lower()
            boards = [
                b
                for b in boards
                if q in b.get("name", "").lower()
                or q in b.get("board_id", "").lower()
                or q in (b.get("description") or "").lower()
            ]

        if json_mode:
            print_result(boards, json_mode=True)
            return

        if not boards:
            console.print("[yellow]No boards found.[/yellow]")
            return

        console.print(render_boards_table(boards))
        console.print(
            "\n[dim]Tip: View a board with [bold cyan]ak5 board <board_id>[/bold cyan]; "
            "create with [bold cyan]ak5 board create <id> --template default[/bold cyan][/dim]"
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as e:
        handle_client_error(e, json_mode=json_mode)


def _create_board_impl(
    board_id: str,
    name: str,
    description: str | None,
    template: str,
    owners: tuple[str, ...],
    *,
    json_mode: bool,
    deprecated_alias: bool = False,
) -> None:
    payload: dict[str, object] = {
        "board_id": board_id,
        "name": name,
        "description": description,
        "template": template,
    }
    if owners:
        payload["owner_actor_ids"] = list(owners)

    try:
        with open_client() as client:
            board = client.create_board(payload)
        if json_mode:
            print_result(board, json_mode=True)
            return
        if deprecated_alias:
            console.print(
                "[yellow]Deprecated:[/yellow] use [cyan]ak5 board create[/cyan] instead of create-board."
            )
        console.print(
            f"[bold green]✓[/bold green] Board [cyan bold]{board['board_id']}[/cyan bold] created "
            f"(template={board.get('template')})!"
        )
        console.print(f"  Name: [bold white]{board['name']}[/bold white]")
        if board.get("description"):
            console.print(f"  Description: {board['description']}")
        if owners:
            console.print(f"  Owners: {', '.join(f'@{o}' for o in owners)}")
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as e:
        handle_client_error(e, json_mode=json_mode)


def _adopt_board_impl(board_id: str, *, json_mode: bool, deprecated_alias: bool = False) -> None:
    try:
        with open_client() as client:
            data = client.adopt_board(board_id)
        if json_mode:
            print_result(data, json_mode=True)
            return
        if deprecated_alias:
            console.print(
                "[yellow]Deprecated:[/yellow] use [cyan]ak5 board adopt[/cyan] instead of claim-board."
            )
        console.print(
            f"[bold green]✓[/bold green] {data.get('message', 'Claimed')} "
            f"— [cyan bold]{data['board_id']}[/cyan bold] as @{data['actor_id']} ({data['role']})"
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as e:
        handle_client_error(e, json_mode=json_mode)


@click.command(
    "create-board",
    deprecated=True,
    epilog="Deprecated alias of `ak5 board create <id> --template <name>`.",
)
@click.argument("board_id")
@click.option("--name", "-n", required=True, help="Board display name")
@click.option("--desc", "-d", "description", default=None, help="Board description")
@click.option("--template", default="default", help="Board template (default: default)")
@click.option(
    "--owner",
    "owners",
    multiple=True,
    help="Human actor ID to enroll as board admin (repeatable).",
)
@click.pass_context
def create_board_command(
    ctx: click.Context,
    board_id: str,
    name: str,
    description: str | None,
    template: str,
    owners: tuple[str, ...],
) -> None:
    """Deprecated alias of ``ak5 board create``."""
    _create_board_impl(
        board_id,
        name,
        description,
        template,
        owners,
        json_mode=is_json_mode(flag=json_flag_from_ctx(ctx)),
        deprecated_alias=True,
    )


@click.command(
    "claim-board",
    deprecated=True,
    epilog="Deprecated alias of `ak5 board adopt <id>`.",
)
@click.argument("board_id")
@click.pass_context
def claim_board_command(ctx: click.Context, board_id: str) -> None:
    """Deprecated alias of ``ak5 board adopt``."""
    _adopt_board_impl(
        board_id,
        json_mode=is_json_mode(flag=json_flag_from_ctx(ctx)),
        deprecated_alias=True,
    )
