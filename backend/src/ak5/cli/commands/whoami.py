import click
import httpx
from ak5.cli.config import (
    get_api_url,
    list_identity_hints,
    print_identity_hints,
    resolve_session,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.command("whoami")
def whoami_command() -> None:
    """Display current authenticated Actor session identity."""
    resolved = resolve_session()
    project_root = resolved.project_root

    if resolved.ambiguous:
        print_identity_hints(resolved)
        raise SystemExit(1)

    session = resolved.data
    actor_id = session.get("actor_id")
    token = session.get("token")
    api_url = get_api_url()

    if not actor_id:
        console.print("[yellow]No active actor session found.[/yellow]")
        hints = list_identity_hints(project_root)
        if hints:
            print_identity_hints(resolved)
        else:
            console.print(
                "[dim]Run [bold cyan]ak5 login --id <actor_id> --role <role>[/bold cyan] "
                "to authenticate.[/dim]"
            )
        raise SystemExit(1)

    if not token and resolved.source == "env_actor":
        console.print(
            f"[yellow]AK5_ACTOR_ID={actor_id} is set but no session file found.[/yellow]"
        )
        console.print(
            f"[dim]Expected:[/dim] {resolved.path}\n"
            f"[dim]Run:[/dim] ak5 login --id {actor_id} --role \"...\" --caps \"...\""
        )
        raise SystemExit(1)

    if not token:
        console.print(f"[yellow]Identity @{actor_id} found but JWT token missing.[/yellow]")
        console.print(
            f"[dim]Re-authenticate:[/dim] ak5 login --id {actor_id} --role "
            f"\"{session.get('role') or '...'}\" --caps \"...\""
        )
        raise SystemExit(1)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Actor ID", f"@{actor_id}")
    table.add_row("Role", session.get("role", "Unknown"))
    table.add_row("Type", session.get("actor_type", "agent"))
    table.add_row("API Gateway", api_url)
    table.add_row("Source", resolved.source)
    if project_root:
        table.add_row("Project Root", str(project_root))
    if resolved.path:
        table.add_row("Session Path", str(resolved.path))
    table.add_row("Token Status", "[green]Authenticated (JWT Present)[/green]")

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

    console.print(Panel(table, title="Current AK5 Session", expand=False))
