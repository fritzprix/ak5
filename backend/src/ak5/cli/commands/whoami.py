"""``ak5 whoami`` — show daemon profile or legacy project session."""

from __future__ import annotations

import os

import click
import httpx
from ak5.cli.config import (
    get_api_url,
    list_identity_hints,
    print_identity_hints,
    resolve_session,
)
from ak5.client.errors import ClientError
from ak5.daemon.credentials import load_credential
from ak5.daemon.ipc import fetch_token
from ak5.daemon.profiles import get_profile, resolve_profile_name
from ak5.daemon.server import is_daemon_running
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _whoami_from_profile(profile_name: str) -> bool:
    """Print daemon profile whoami. Return True if handled."""
    profile = get_profile(profile_name)
    cred = load_credential(profile_name)
    if profile is None and cred is None:
        return False

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")
    table.add_row("Profile", profile_name)
    if profile is not None:
        table.add_row("Server", profile.server or "(unset)")
        table.add_row("Principal", profile.principal)
        table.add_row("Role", profile.role or "(unset)")
        table.add_row("Workspace", profile.workspace or "(default)")
        table.add_row("Actor ID", profile.actor_id or "(unset)")
    if cred is not None:
        table.add_row("Credential", cred.status)
        table.add_row("Expires", cred.expires_at or "(none)")
        table.add_row("Token type", "opaque" if cred.access_token.startswith("ak5_agt_") else "jwt")
        source = "daemon"
    else:
        table.add_row("Credential", "missing")
        source = "profile-only"

    env_token = os.environ.get("AK5_TOKEN") or os.environ.get("AK5_ACTOR_TOKEN")
    if env_token:
        source = "AK5_TOKEN"
        table.add_row("Env token", "set")

    if is_daemon_running() and cred is not None and cred.status == "active":
        try:
            tok = fetch_token(profile_name, ensure=False)
            table.add_row("Daemon token", "ok")
            if tok.get("actor_id"):
                table.add_row("Daemon actor", str(tok["actor_id"]))
        except ClientError as exc:
            table.add_row("Daemon token", f"[red]{exc.message}[/red]")

    table.add_row("Source", source)
    console.print(Panel(table, title="AK5 Whoami", expand=False))
    return True


@click.command("whoami")
@click.option("--profile", default=None, help="Daemon profile to inspect")
def whoami_command(profile: str | None) -> None:
    """Show the active daemon profile or project-bound actor session.

    Agents:
      - Prefer whoami over inventing a new --id.
      - Copy the plain Actor ID (no leading @) into AK5_ACTOR_ID.
      - If ambiguous, match role/caps under .ak5/identity/, then export + whoami.
      - Next after a healthy session: ak5 subscribe create … && ak5 subscribe ls
    """
    profile_name = resolve_profile_name(profile)
    if profile_name and _whoami_from_profile(profile_name):
        return

    if os.environ.get("AK5_TOKEN") or os.environ.get("AK5_ACTOR_TOKEN"):
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Value", style="white")
        table.add_row("Source", "AK5_TOKEN")
        table.add_row("API Gateway", get_api_url())
        table.add_row("Token Status", "[green]env token present[/green]")
        console.print(Panel(table, title="AK5 Whoami", expand=False))
        return

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
        console.print("[yellow]No active profile or actor session found.[/yellow]")
        hints = list_identity_hints(project_root)
        if hints:
            print_identity_hints(resolved)
        else:
            console.print(
                "[dim]Run [bold cyan]ak5 login --agent <name> --role worker[/bold cyan] "
                "or [bold cyan]ak5 login --profile me[/bold cyan].[/dim]"
            )
        raise SystemExit(1)

    if not token and resolved.source == "env_actor":
        console.print(
            f"[yellow]AK5_ACTOR_ID={actor_id} is set but no session file found.[/yellow]"
        )
        raise SystemExit(1)

    if not token:
        console.print(f"[yellow]Identity @{actor_id} found but JWT token missing.[/yellow]")
        raise SystemExit(1)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")
    table.add_row("Actor ID", str(actor_id))
    table.add_row("Mention", f"@{actor_id}")
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
    console.print(
        f"[dim]Bind shells:[/dim] export AK5_ACTOR_ID={actor_id}\n"
        "[dim]Next (agents):[/dim] ak5 subscribe create <BOARD> "
        f"--for-agent {actor_id} --exec '<YOUR_HARNESS_WAKE>' && ak5 subscribe ls"
    )
