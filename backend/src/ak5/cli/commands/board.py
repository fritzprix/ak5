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
        wip_info = f" [WIP: {count}/{col['wip_limit']}]" if col.get("wip_limit", 0) > 0 else f" ({count})"
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

                content = (
                    f"[bold white]{t['title']}[/bold white]\n"
                    f"{status_badge} [dim]{t['ticket_id']}[/dim] | {assignee}"
                    f"{subtask_badge}"
                )

                border_color = "yellow" if col.get("stage") == "in_progress" else "white"
                row_cells.append(Panel(content, border_style=border_color, expand=True))
            else:
                row_cells.append(Text(""))
        main_table.add_row(*row_cells)

    return main_table


def fetch_board(api_url: str, board_id: str) -> dict:
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{api_url}/boards/{board_id}")
        resp.raise_for_status()
        return resp.json()


async def watch_board_live(api_url: str, board_id: str) -> None:
    """Stream SSE events and re-render board on changes."""
    console.print(f"[bold cyan]Connecting to real-time event stream for board '{board_id}'...[/bold cyan]")
    board_data = fetch_board(api_url, board_id)

    with Live(render_board_view(board_data), console=console, refresh_per_second=4) as live:
        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream("GET", f"{api_url}/events/stream") as stream,
        ):
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    if event_type in ("TICKET_CREATED", "TICKET_MOVED", "TICKET_DELEGATED", "TICKET_UPDATED", "COMMENT_ADDED"):
                        # Refresh board
                        try:
                            board_data = fetch_board(api_url, board_id)
                            live.update(render_board_view(board_data))
                        except (httpx.HTTPError, OSError):
                            pass


@click.command("board")
@click.option("--board-id", default="proj-core-engine", help="Target Board ID")
@click.option("--watch", is_flag=True, help="Watch board with live real-time SSE updates")
def board_command(board_id: str, watch: bool) -> None:
    """View Kanban board in terminal."""
    api_url = get_api_url()
    try:
        if watch:
            asyncio.run(watch_board_live(api_url, board_id))
        else:
            board_data = fetch_board(api_url, board_id)
            console.print(render_board_view(board_data))
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Board query failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch stopped.[/yellow]")
