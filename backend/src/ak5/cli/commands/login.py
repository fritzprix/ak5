import click
import httpx
from ak5.cli.config import (
    find_project_root,
    get_api_url,
    identity_path_for,
    list_identity_hints,
    save_session,
)
from rich.console import Console
from rich.table import Table

console = Console()


def _print_identity_hints(project_root) -> None:
    hints = list_identity_hints(project_root)
    if not hints:
        return
    console.print(
        f"[yellow]Local identity claims under[/yellow] "
        f"[cyan]{project_root / '.ak5' / 'identity'}[/cyan]"
    )
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Actor")
    table.add_column("Role")
    table.add_column("Capabilities")
    for h in hints:
        caps = h.get("capabilities") or []
        caps_str = ", ".join(caps) if isinstance(caps, list) else str(caps)
        table.add_row(f"@{h['actor_id']}", str(h.get("role") or ""), caps_str)
    console.print(table)
    console.print(
        "[dim]Match against your role/capabilities, then: "
        "export AK5_ACTOR_ID=<id> && ak5 whoami[/dim]"
    )


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
    project_root = find_project_root()

    existing = list_identity_hints(project_root)
    if len(existing) >= 1 and actor_id not in {h["actor_id"] for h in existing}:
        console.print("[dim]Other local identities already present:[/dim]")
        _print_identity_hints(project_root)

    payload = {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "name": actor_id.replace("-", " ").replace("_", " ").title(),
        "role": role,
        "capabilities": capabilities,
    }

    try:
        from ak5.security import identify_headers_from_env

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{api_url}/auth/identify",
                json=payload,
                headers=identify_headers_from_env(),
            )
            resp.raise_for_status()
            data = resp.json()

        token = data["access_token"]
        session_path = save_session(
            {
                "token": token,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "role": role,
                "api_url": api_url,
                "capabilities": capabilities,
            },
            project_root=project_root,
        )
        claim_path = identity_path_for(actor_id, project_root)
        console.print(f"[bold green]✓[/bold green] Authenticated as [cyan]{actor_id}[/cyan] [{actor_type}]")
        console.print(f"[dim]Session:[/dim]  {session_path}")
        console.print(f"[dim]Identity:[/dim] {claim_path}")
        console.print(
            "[dim]Tip: export AK5_ACTOR_ID="
            f"{actor_id}"
            " so ephemeral shells bind to this identity.[/dim]"
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
        console.print("Make sure the backend server is running: [yellow]uvicorn ak5.main:app[/yellow]")
    except Exception as e:
        console.print(f"[bold red]✗ Login failed:[/bold red] {e}")
