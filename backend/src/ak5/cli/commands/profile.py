"""``ak5 profile`` commands (client.md §2)."""

from __future__ import annotations

import json

import click
from ak5.daemon.profiles import (
    Profile,
    list_profiles,
    load_profiles_file,
    resolve_profile_name,
    set_default_profile,
    upsert_profile,
)
from rich.console import Console

console = Console()


@click.group("profile")
def profile_command() -> None:
    """Manage client profiles (server URL + principal)."""


@profile_command.command("list")
@click.pass_context
def profile_list(ctx: click.Context) -> None:
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    data = load_profiles_file()
    rows = [
        {
            "name": p.name,
            "server": p.server,
            "workspace": p.workspace,
            "principal": p.principal,
            "role": p.role,
            "default": p.name == data.get("default_profile"),
        }
        for p in list_profiles()
    ]
    if json_mode:
        click.echo(json.dumps({"default_profile": data.get("default_profile"), "profiles": rows}))
        return
    if not rows:
        console.print("[dim]no profiles — create with `ak5 profile use` / login[/dim]")
        return
    for r in rows:
        mark = "*" if r["default"] else " "
        console.print(
            f"{mark} [cyan]{r['name']}[/cyan]  {r['server']}  "
            f"({r['principal']}/{r['role'] or '-'})"
        )


@profile_command.command("use")
@click.argument("name")
@click.option("--server", default=None, help="Server base URL (https://… or http://…/api/v1)")
@click.option("--workspace", default="", help="Workspace id")
@click.option("--principal", default="agent", type=click.Choice(["agent", "user", "human"]))
@click.option("--role", default="")
@click.option("--actor-id", default="")
def profile_use(
    name: str,
    server: str | None,
    workspace: str,
    principal: str,
    role: str,
    actor_id: str,
) -> None:
    """Select default profile; create it if missing (requires --server on create)."""
    existing = {p.name: p for p in list_profiles()}
    if name in existing and server is None:
        set_default_profile(name)
        console.print(f"[green]default profile[/green] → {name}")
        return
    if server is None:
        raise click.UsageError("--server is required when creating a profile")
    upsert_profile(
        Profile(
            name=name,
            server=server,
            workspace=workspace,
            principal="human" if principal == "user" else principal,
            role=role,
            actor_id=actor_id,
        )
    )
    set_default_profile(name)
    console.print(f"[green]profile[/green] {name} ready (default)")


@profile_command.command("show")
@click.argument("name", required=False, default=None)
@click.pass_context
def profile_show(ctx: click.Context, name: str | None) -> None:
    resolved = resolve_profile_name(name)
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    if not resolved:
        raise click.ClickException("no profile selected")
    match = next((p for p in list_profiles() if p.name == resolved), None)
    if match is None:
        raise click.ClickException(f"profile '{resolved}' not found")
    body = {
        "name": match.name,
        "server": match.server,
        "workspace": match.workspace,
        "principal": match.principal,
        "role": match.role,
        "actor_id": match.actor_id,
    }
    if json_mode:
        click.echo(json.dumps(body))
    else:
        console.print(body)
