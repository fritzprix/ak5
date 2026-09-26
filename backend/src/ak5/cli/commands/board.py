import asyncio

import click
import httpx
from ak5.cli.config import get_api_url
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


def render_board_view(board_data: dict) -> Table:
    """Render 4-column Kanban board as a Rich Table."""
    main_table = Table(
        title=f"📋 Kanban Board: {board_data['name']} ({board_data['board_id']})",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )

    stage_colors = {
        "open": "blue",
        "in_progress": "yellow",
        "review": "magenta",
        "done": "green",
    }

    # Add column headers
    for col in board_data.get("columns", []):
        color = stage_colors.get(col.get("stage"), "white")
        count = len(col.get("tickets", []))
        total = col.get("total_ticket_count", count)
        if col.get("wip_limit", 0) > 0:
            wip_info = f" [WIP: {count}/{col['wip_limit']}]"
        elif total > count:
            wip_info = f" ({count}/{total})"
        else:
            wip_info = f" ({count})"
        main_table.add_column(f"[{color}]{col['name']}{wip_info}[/{color}]")

    # Find maximum number of rows among columns
    max_tickets = max(len(col.get("tickets", [])) for col in board_data.get("columns", [])) if board_data.get("columns") else 0

    if max_tickets == 0:
        empty_row = [Panel("[dim](Empty)[/dim]", expand=True) for _ in board_data.get("columns", [])]
        main_table.add_row(*empty_row)
        return main_table

    for row_idx in range(max_tickets):
        row_cells = []
        for col in board_data.get("columns", []):
            tickets = col.get("tickets", [])
            if row_idx < len(tickets):
                t = tickets[row_idx]
                p_color = "red" if t["priority"] in ("urgent", "high") else ("yellow" if t["priority"] == "medium" else "dim")
                status_badge = f"[{p_color}][{t['priority'].upper()}][/{p_color}]"

                assignee = f"@{t['assigned_to']}" if t.get("assigned_to") else "[dim]Unassigned[/dim]"

                subtask_badge = ""
                if t.get("subtask_count", 0) > 0:
                    subtask_badge = f"\n[bold cyan]Subtasks: {t['subtask_done_count']}/{t['subtask_count']} Done[/bold cyan]"

                archived_badge = " [bold red][ARCHIVED][/bold red]" if t.get("is_archived") else ""

                content = (
                    f"[bold white]{t['title']}[/bold white]{archived_badge}\n"
                    f"{status_badge} [dim]{t['ticket_id']}[/dim] | {assignee}"
                    f"{subtask_badge}"
                )

                border_color = "yellow" if col.get("stage") == "in_progress" else "white"
                row_cells.append(Panel(content, border_style=border_color, expand=True))
            else:
                row_cells.append(Text(""))
        main_table.add_row(*row_cells)

    # Check if any column was limited
    has_more = any(col.get("total_ticket_count", 0) > len(col.get("tickets", [])) for col in board_data.get("columns", []))
    if has_more:
        more_cells = []
        for col in board_data.get("columns", []):
            diff = col.get("total_ticket_count", 0) - len(col.get("tickets", []))
            if diff > 0:
                more_cells.append(Panel(f"[dim]+ {diff} more tickets\n(--done-limit=0 to see all)[/dim]", border_style="dim", expand=True))
            else:
                more_cells.append(Text(""))
        main_table.add_row(*more_cells)

    return main_table


def fetch_board(api_url: str, board_id: str, include_archived: bool = False, done_limit: int | None = 10) -> dict:
    params: dict[str, Any] = {}
    if include_archived:
        params["include_archived"] = "true"
    if done_limit is not None and done_limit > 0:
        params["done_limit"] = done_limit
    elif done_limit == 0:
        params["done_limit"] = 0

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{api_url}/boards/{board_id}", params=params)
        resp.raise_for_status()
        return resp.json()


async def watch_board_live(api_url: str, board_id: str, include_archived: bool = False, done_limit: int | None = 10) -> None:
    """Stream SSE events and re-render board on changes."""
    console.print(f"[bold cyan]Connecting to real-time event stream for board '{board_id}'...[/bold cyan]")
    board_data = fetch_board(api_url, board_id, include_archived=include_archived, done_limit=done_limit)

    with Live(render_board_view(board_data), console=console, refresh_per_second=4) as live:
        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream("GET", f"{api_url}/events/stream") as stream,
        ):
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    if event_type in ("TICKET_CREATED", "TICKET_MOVED", "TICKET_DELEGATED", "TICKET_UPDATED", "TICKET_ARCHIVED", "TICKET_UNARCHIVED", "COMMENT_ADDED"):
                        # Refresh board
                        try:
                            board_data = fetch_board(api_url, board_id, include_archived=include_archived, done_limit=done_limit)
                            live.update(render_board_view(board_data))
                        except (httpx.HTTPError, OSError):
                            pass


@click.command("board")
@click.argument("target_board", required=False, default=None, metavar="[BOARD_ID]")
@click.option("--board-id", default=None, help="Target Board ID (alternative to positional argument)")
@click.option("--watch", is_flag=True, help="Watch board with live real-time SSE updates")
@click.option("--list", "-l", "list_boards_flag", is_flag=True, help="List all available Kanban boards")
@click.option("--done-limit", default=10, type=int, help="Limit completed tickets shown in Done column (default: 10, 0 for all)")
@click.option("--include-archived", is_flag=True, default=False, help="Include archived tickets in board view")
def board_command(
    target_board: str | None,
    board_id: str | None,
    watch: bool,
    list_boards_flag: bool,
    done_limit: int,
    include_archived: bool,
) -> None:
    """View Kanban board in terminal.

    Optionally specify BOARD_ID positionally (e.g. 'ak5 board proj-harbor-eval')
    or list boards using 'ak5 board list' or 'ak5 boards'.
    """
    api_url = get_api_url()

    # Handle 'ak5 board list' or 'ak5 board --list'
    if list_boards_flag or target_board == "list":
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(f"{api_url}/boards")
                resp.raise_for_status()
                boards = resp.json()

            if not boards:
                console.print("[yellow]No boards found.[/yellow]")
                return

            from ak5.cli.commands.boards import render_boards_table
            console.print(render_boards_table(boards))
            console.print(
                "\n[dim]💡 Tip: View a specific board with [bold cyan]ak5 board <board_id>[/bold cyan] "
                "(e.g. [cyan]ak5 board proj-harbor-eval[/cyan])[/dim]"
            )
            return
        except httpx.ConnectError:
            console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
            return
        except Exception as e:
            console.print(f"[bold red]✗ Error querying boards:[/bold red] {e}")
            return

    resolved_board_id = target_board or board_id or "proj-core-engine"

    try:
        if watch:
            asyncio.run(watch_board_live(api_url, resolved_board_id, include_archived=include_archived, done_limit=done_limit))
        else:
            board_data = fetch_board(api_url, resolved_board_id, include_archived=include_archived, done_limit=done_limit)
            console.print(render_board_view(board_data))
            console.print(
                f"[dim]💡 Active Board: [bold cyan]{board_data['board_id']}[/bold cyan] | "
                "Run [bold cyan]ak5 boards[/bold cyan] (or [bold cyan]ak5 board list[/bold cyan]) to view all available boards[/dim]"
            )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Board query failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch stopped.[/yellow]")
