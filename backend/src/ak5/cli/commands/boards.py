import click
import httpx
from ak5.cli.config import get_api_url
from rich.console import Console
from rich.table import Table

console = Console()


def render_boards_table(boards: list[dict]) -> Table:
    table = Table(title="📋 Available Kanban Boards", show_lines=True)
    table.add_column("Board ID", style="cyan bold", no_wrap=True)
    table.add_column("Name", style="magenta bold")
    table.add_column("Description", style="white")
    table.add_column("Created By", style="green")

    for b in boards:
        table.add_row(
            b["board_id"],
            b["name"],
            b.get("description") or "[dim]N/A[/dim]",
            f"@{b['created_by']}",
        )
    return table


@click.command("boards")
@click.option("--query", "-q", default=None, help="Filter boards by name or description")
def boards_command(query: str | None) -> None:
    """List all available Kanban boards."""
    api_url = get_api_url()
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{api_url}/boards")
            resp.raise_for_status()
            boards = resp.json()

        if query:
            q = query.lower()
            boards = [
                b
                for b in boards
                if q in b.get("name", "").lower()
                or q in b.get("board_id", "").lower()
                or q in (b.get("description") or "").lower()
            ]

        if not boards:
            console.print("[yellow]No boards found.[/yellow]")
            return

        console.print(render_boards_table(boards))
        console.print(
            "\n[dim]💡 Tip: View a specific board with [bold cyan]ak5 board <board_id>[/bold cyan] "
            "(e.g. [cyan]ak5 board proj-harbor-eval[/cyan])[/dim]"
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]✗ Error querying boards:[/bold red] {e}")


@click.command("create-board")
@click.argument("board_id")
@click.option("--name", "-n", required=True, help="Board display name")
@click.option("--desc", "-d", "description", default=None, help="Board description")
def create_board_command(board_id: str, name: str, description: str | None) -> None:
    """Create a new Kanban project board with standard default columns."""
    from ak5.cli.config import get_auth_headers

    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    payload = {
        "board_id": board_id,
        "name": name,
        "description": description,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_url}/boards", json=payload, headers=headers)
            resp.raise_for_status()
            board = resp.json()

        console.print(f"[bold green]✓[/bold green] Board [cyan bold]{board['board_id']}[/cyan bold] created successfully!")
        console.print(f"  Name: [bold white]{board['name']}[/bold white]")
        if board.get("description"):
            console.print(f"  Description: {board['description']}")
        console.print(f"\n[dim]💡 View the board with [bold cyan]ak5 board {board_id}[/bold cyan][/dim]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Board creation failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error creating board:[/bold red] {e}")

