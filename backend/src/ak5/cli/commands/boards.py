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


@click.command(
    "create-board",
    epilog=(
        "Examples:\n"
        "  ak5 create-board proj-mobile-app --name \"Mobile App\" --desc \"iOS/Android\"\n"
        "  ak5 create-board proj-agent-owned --name \"Agent Project\" --owner user_pm\n"
        "  ak5 create-board proj-shared --name \"Shared\" --owner user_pm --owner user_other"
    ),
)
@click.argument("board_id")
@click.option("--name", "-n", required=True, help="Board display name")
@click.option("--desc", "-d", "description", default=None, help="Board description")
@click.option(
    "--owner",
    "owners",
    multiple=True,
    help="Human actor ID to enroll as board admin (repeatable). Prefer this when an agent creates a board.",
)
def create_board_command(
    board_id: str, name: str, description: str | None, owners: tuple[str, ...]
) -> None:
    """Create a new Kanban project board with standard default columns.

    When an agent creates a board, pass --owner <human_id> so a human gets
    board admin immediately. For boards already created without a human admin,
    use `ak5 claim-board`.
    """
    from ak5.cli.config import require_auth_headers

    api_url = get_api_url()
    headers = require_auth_headers(api_url)

    payload: dict[str, object] = {
        "board_id": board_id,
        "name": name,
        "description": description,
    }
    if owners:
        payload["owner_actor_ids"] = list(owners)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_url}/boards", json=payload, headers=headers)
            resp.raise_for_status()
            board = resp.json()

        console.print(f"[bold green]✓[/bold green] Board [cyan bold]{board['board_id']}[/cyan bold] created successfully!")
        console.print(f"  Name: [bold white]{board['name']}[/bold white]")
        if board.get("description"):
            console.print(f"  Description: {board['description']}")
        if owners:
            console.print(f"  Owners: {', '.join(f'@{o}' for o in owners)}")
        console.print(f"\n[dim]💡 View the board with [bold cyan]ak5 board {board_id}[/bold cyan][/dim]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Board creation failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error creating board:[/bold red] {e}")


@click.command(
    "claim-board",
    epilog=(
        "Example:\n"
        "  ak5 claim-board proj-agent-owned\n\n"
        "Only human actors can claim. Fails with 409 if a human admin already exists."
    ),
)
@click.argument("board_id")
def claim_board_command(board_id: str) -> None:
    """Claim board admin as a human when the board has no human admin yet."""
    from ak5.cli.config import require_auth_headers

    api_url = get_api_url()
    headers = require_auth_headers(api_url)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_url}/boards/{board_id}/claim", headers=headers)
            resp.raise_for_status()
            data = resp.json()

        console.print(
            f"[bold green]✓[/bold green] {data.get('message', 'Claimed')} "
            f"— [cyan bold]{data['board_id']}[/cyan bold] as @{data['actor_id']} ({data['role']})"
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Claim failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error claiming board:[/bold red] {e}")

