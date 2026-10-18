"""``ak5 whoami`` — show daemon profile or legacy project session."""

from __future__ import annotations

import os

import click
import httpx
from ak5.cli.client_factory import json_flag_from_ctx
from ak5.cli.config import (
    get_api_url,
    list_identity_hints,
    print_identity_hints,
    resolve_session,
)
from ak5.client.errors import ClientError
from ak5.client.output import is_json_mode, print_result
from ak5.daemon.credentials import load_credential
from ak5.daemon.ipc import fetch_token
from ak5.daemon.profiles import get_profile, resolve_profile_name
from ak5.daemon.server import is_daemon_running
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _profile_payload(profile_name: str) -> dict | None:
    profile = get_profile(profile_name)
    cred = load_credential(profile_name)
    if profile is None and cred is None:
        return None
    out: dict = {
        "profile": profile_name,
        "source": "daemon",
    }
    if profile is not None:
        out.update(
            {
                "server": profile.server,
                "principal": profile.principal,
                "role": profile.role,
                "workspace": profile.workspace,
                "actor_id": profile.actor_id,
            }
        )
    if cred is not None:
        out["credential_status"] = cred.status
        out["expires_at"] = cred.expires_at
        out["token_type"] = "opaque" if cred.access_token.startswith("ak5_agt_") else "jwt"
    else:
        out["credential_status"] = "missing"
        out["source"] = "profile-only"
    if os.environ.get("AK5_TOKEN") or os.environ.get("AK5_ACTOR_TOKEN"):
        out["source"] = "AK5_TOKEN"
        out["env_token"] = True
    if is_daemon_running() and cred is not None and cred.status == "active":
        try:
            tok = fetch_token(profile_name, ensure=False)
            out["daemon_token"] = "ok"
            if tok.get("actor_id"):
                out["daemon_actor_id"] = tok["actor_id"]
        except ClientError as exc:
            out["daemon_token"] = exc.message
    return out


def _whoami_from_profile(profile_name: str, *, json_mode: bool) -> bool:
    """Print daemon profile whoami. Return True if handled."""
    payload = _profile_payload(profile_name)
    if payload is None:
        return False
    if json_mode:
        print_result(payload, json_mode=True)
        return True

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")
    table.add_row("Profile", profile_name)
    for key, label in (
        ("server", "Server"),
        ("principal", "Principal"),
        ("role", "Role"),
        ("workspace", "Workspace"),
        ("actor_id", "Actor ID"),
        ("credential_status", "Credential"),
        ("expires_at", "Expires"),
        ("token_type", "Token type"),
        ("daemon_token", "Daemon token"),
        ("daemon_actor_id", "Daemon actor"),
        ("source", "Source"),
    ):
        if key in payload and payload[key] not in (None, ""):
            table.add_row(label, str(payload[key]))
    console.print(Panel(table, title="AK5 Whoami", expand=False))
    return True


@click.command("whoami")
@click.option("--profile", default=None, help="Daemon profile to inspect")
@click.pass_context
def whoami_command(ctx: click.Context, profile: str | None) -> None:
    """Show the active daemon profile or project-bound actor session.

    Agents:
      - Prefer whoami over inventing a new --id.
      - Copy the plain Actor ID (no leading @) into AK5_ACTOR_ID.
      - If ambiguous, match role/caps under .ak5/identity/, then export + whoami.
      - Next after a healthy session: ak5 subscribe create … && ak5 subscribe ls
    """
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    profile_name = resolve_profile_name(profile)
    if profile_name and _whoami_from_profile(profile_name, json_mode=json_mode):
        return

    if os.environ.get("AK5_TOKEN") or os.environ.get("AK5_ACTOR_TOKEN"):
        payload = {
            "source": "AK5_TOKEN",
            "api_url": get_api_url(),
            "token_status": "env token present",
        }
        if json_mode:
            print_result(payload, json_mode=True)
            return
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Value", style="white")
        table.add_row("Source", "AK5_TOKEN")
        table.add_row("API Gateway", payload["api_url"])
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

    payload = {
        "actor_id": actor_id,
        "role": session.get("role", "Unknown"),
        "actor_type": session.get("actor_type", "agent"),
        "api_url": api_url,
        "source": resolved.source,
        "token_status": "authenticated",
    }
    if json_mode:
        print_result(payload, json_mode=True)
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")
    table.add_row("Actor ID", str(actor_id))
    table.add_row("Mention", f"@{actor_id}")
    table.add_row("Role", str(payload["role"]))
    table.add_row("Type", str(payload["actor_type"]))
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
