import click
import httpx
from rich.console import Console
from ak5.cli.config import get_api_url, save_session

console = Console()


@click.command("login")
@click.option("--id", "actor_id", required=True, help="Actor ID (e.g. agent-code-reviewer, user_pm)")
@click.option("--role", required=True, help="Display role (e.g. 'Senior Reviewer', 'PM')")
@click.option("--caps", default="", help="Comma-separated capabilities (e.g. 'python,rust,security')")
@click.option("--type", "actor_type", type=click.Choice(["human", "agent"]), default="agent", help="Actor type")
@click.option("--url", default=None, help="AK5 Gateway API URL")
def login_command(actor_id: str, role: str, caps: str, actor_type: str, url: str | None) -> None:
    """Identify and authenticate as an Actor (Human or AI Agent)."""
    api_url = url or get_api_url()
    capabilities = [c.strip() for c in caps.split(",") if c.strip()]

    payload = {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "name": actor_id.replace("-", " ").replace("_", " ").title(),
        "role": role,
        "capabilities": capabilities,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_url}/auth/identify", json=payload)
            resp.raise_for_status()
            data = resp.json()

        token = data["access_token"]
        save_session({
            "token": token,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "role": role,
            "api_url": api_url,
        })
        console.print(f"[bold green]✓[/bold green] Authenticated as [cyan]{actor_id}[/cyan] [{actor_type}]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
        console.print("Make sure the backend server is running: [yellow]uvicorn ak5.main:app[/yellow]")
    except Exception as e:
        console.print(f"[bold red]✗ Login failed:[/bold red] {e}")
