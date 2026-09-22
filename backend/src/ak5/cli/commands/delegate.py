import click
import httpx
from ak5.cli.config import get_api_url, get_token
from rich.console import Console

console = Console()


@click.command("delegate")
@click.argument("ticket_id")
@click.option("--to", "target_actor_id", required=True, help="Target Agent ID to delegate task to")
@click.option("--title", required=True, help="Subtask title")
@click.option("--desc", "description", default="", help="Subtask detailed description")
@click.option("--priority", type=click.Choice(["low", "medium", "high", "urgent"]), default="medium")
def delegate_command(
    ticket_id: str,
    target_actor_id: str,
    title: str,
    description: str,
    priority: str,
) -> None:
    """Delegate a subtask to an agent under an existing parent ticket."""
    api_url = get_api_url()
    token = get_token()

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        # Auto-login as orchestrator if not logged in
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{api_url}/auth/identify",
                json={"actor_id": "cli_user", "actor_type": "human", "name": "CLI User", "role": "PM"},
            )
            if resp.is_success:
                headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

    payload = {
        "target_actor_id": target_actor_id,
        "subtask_title": title,
        "subtask_description": description,
        "priority": priority,
        "labels": ["delegated", "cli"],
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{api_url}/tickets/{ticket_id}/delegate",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            subtask = resp.json()

        console.print(
            f"[bold green]✓[/bold green] Subtask [cyan]{subtask['ticket_id']}[/cyan] created and assigned to "
            f"[magenta]@{target_actor_id}[/magenta] (Parent: [yellow]{ticket_id}[/yellow])"
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Delegation failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")
