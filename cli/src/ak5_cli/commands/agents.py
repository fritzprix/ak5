import click
import httpx
from rich.console import Console
from rich.table import Table
from ak5_cli.config import get_api_url

console = Console()


@click.command("agents")
@click.option("--cap", default=None, help="Filter by capability tag (e.g. 'image-resize')")
@click.option("--status", default=None, help="Filter by status ('idle', 'busy', 'offline')")
@click.option("--query", default=None, help="Semantic search query")
def agents_command(cap: str | None, status: str | None, query: str | None) -> None:
    """Discover agents matching capability tags or search queries."""
    api_url = get_api_url()
    params = {}
    if cap:
        params["capability"] = cap
    if status:
        params["status"] = status
    if query:
        params["query"] = query

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{api_url}/actors/discovery", params=params)
            resp.raise_for_status()
            agents = resp.json()

        if not agents:
            console.print("[yellow]No matching agents found.[/yellow]")
            return

        table = Table(title="Available Autonomous AI Agents", show_lines=True)
        table.add_column("Agent ID", style="cyan", no_wrap=True)
        table.add_column("Role", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Capabilities", style="yellow")

        for a in agents:
            caps_str = ", ".join(a.get("capabilities", []))
            status_color = "green" if a["status"] == "idle" else ("yellow" if a["status"] == "busy" else "red")
            table.add_row(
                f"@{a['actor_id']}",
                a["role"],
                f"[{status_color}]{a['status']}[/{status_color}]",
                caps_str,
            )

        console.print(table)
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]✗ Error querying agents:[/bold red] {e}")
