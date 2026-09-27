import click
from ak5.cli.config import clear_session, find_project_root, get_actor_id, list_identity_hints
from rich.console import Console

console = Console()


@click.command("logout")
@click.option("--id", "actor_id", default=None, help="Actor ID whose local session/claim to remove")
@click.option("--all", "all_actors", is_flag=True, help="Remove all local .ak5 sessions and identity claims")
def logout_command(actor_id: str | None, all_actors: bool) -> None:
    """Clear local project session and identity claim files (does not call the gateway)."""
    project_root = find_project_root()

    if not all_actors and not actor_id:
        actor_id = get_actor_id()
        if not actor_id:
            hints = list_identity_hints(project_root)
            if len(hints) == 1:
                actor_id = str(hints[0]["actor_id"])
            else:
                console.print("[yellow]Specify --id <actor_id> or --all.[/yellow]")
                if hints:
                    ids = ", ".join(f"@{h['actor_id']}" for h in hints)
                    console.print(f"[dim]Known claims: {ids}[/dim]")
                raise SystemExit(1)

    try:
        removed = clear_session(actor_id, all_actors=all_actors, project_root=project_root)
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise SystemExit(1) from e

    if not removed:
        console.print("[yellow]Nothing to remove.[/yellow]")
        return

    label = ", ".join(f"@{a}" for a in removed)
    console.print(f"[bold green]✓[/bold green] Cleared local identity for {label}")
    console.print(f"[dim]Project:[/dim] {project_root / '.ak5'}")
