"""``ak5 logout`` — revoke server credential + clear local profile / session."""

from __future__ import annotations

import click
from ak5.cli.config import clear_session, find_project_root, get_actor_id, list_identity_hints
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error
from ak5.daemon.ipc import daemon_client, ensure_daemon
from ak5.daemon.profiles import resolve_profile_name
from rich.console import Console

console = Console()


def _logout_profile(profile_name: str) -> None:
    ensure_daemon()
    with daemon_client() as client:
        resp = client.post("/v1/logout", json={"profile": profile_name})
        resp.raise_for_status()
    console.print(f"[bold green]✓[/bold green] Logged out profile [cyan]{profile_name}[/cyan]")


@click.command("logout")
@click.option("--profile", default=None, help="Daemon credential profile to revoke")
@click.option("--id", "actor_id", default=None, help="[legacy] Project session actor id to clear")
@click.option("--all", "all_actors", is_flag=True, help="Remove all local .ak5 sessions and identity claims")
def logout_command(profile: str | None, actor_id: str | None, all_actors: bool) -> None:
    """Revoke daemon credential (server + local) and/or clear project sessions."""
    try:
        if profile and not all_actors and not actor_id:
            _logout_profile(profile.strip())
            return

        profile_name = resolve_profile_name(profile)
        if profile_name and not all_actors and not actor_id and not get_actor_id():
            _logout_profile(profile_name)
            return

        project_root = find_project_root()
        if not all_actors and not actor_id:
            actor_id = get_actor_id()
            if not actor_id:
                hints = list_identity_hints(project_root)
                if len(hints) == 1:
                    actor_id = str(hints[0]["actor_id"])
                elif profile_name:
                    _logout_profile(profile_name)
                    return
                else:
                    console.print(
                        "[yellow]Specify --profile <name>, --id <actor_id>, or --all.[/yellow]"
                    )
                    raise SystemExit(1)

        removed = clear_session(actor_id, all_actors=all_actors, project_root=project_root)
        if not removed:
            console.print("[yellow]Nothing to remove.[/yellow]")
            return
        label = ", ".join(f"@{a}" for a in removed)
        console.print(f"[bold green]✓[/bold green] Cleared local identity for {label}")
    except ClientError as exc:
        handle_client_error(exc, json_mode=False)
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[bold red]✗[/bold red] {exc}")
        raise SystemExit(1) from exc
