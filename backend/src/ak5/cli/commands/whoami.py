import click
import httpx
from ak5.cli.config import get_actor_id, get_api_url, get_token, load_session
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.command("whoami")
def whoami_command() -> None:
    """Display current authenticated Actor session identity."""
    session = load_session()
    actor_id = get_actor_id()
    token = get_token()
    api_url = get_api_url()

    if not actor_id or not token:
        console.print("[yellow]No active actor session found.[/yellow]")
        console.print("[dim]Run [bold cyan]ak5 login --id <actor_id> --role <role>[/bold cyan] to authenticate.[/dim]")
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Actor ID", f"@{actor_id}")
    table.add_row("Role", session.get("role", "Unknown"))
    table.add_row("Type", session.get("actor_type", "agent"))
    table.add_row("API Gateway", api_url)
    table.add_row("Token Status", "[green]Authenticated (JWT Present)[/green]")

    # Attempt to fetch live actor profile from gateway
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(
                f"{api_url}/actors/{actor_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.is_success:
                actor_data = resp.json()
                table.add_row("Live Status", f"[green]{actor_data.get('status', 'idle')}[/green]")
                caps = ", ".join(actor_data.get("capabilities", []))
                if caps:
                    table.add_row("Capabilities", caps)
    except Exception:
        pass

    console.print(Panel(table, title="👤 Current AK5 Session", expand=False))
