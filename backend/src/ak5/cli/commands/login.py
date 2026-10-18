"""``ak5 login`` — OAuth device grant via daemon (client.md §3 / identity.md §3.3·§4.1)."""

from __future__ import annotations

import time
from contextlib import suppress

import click
import httpx
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.cli.config import (
    clean_actor_id,
    find_project_root,
    get_api_url,
    identity_path_for,
    list_identity_hints,
    save_session,
)
from ak5.client.errors import EXIT_AUTH, ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.daemon.ipc import daemon_client, ensure_daemon
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

_ROLE_CHOICES = [r.value for r in Role]
_AGENT_ROLES = [Role.TRIAGE.value, Role.WORKER.value, Role.GATEKEEPER.value]


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


def _normalize_server(url: str) -> str:
    s = url.strip().rstrip("/")
    if not s.endswith("/api/v1"):
        s = f"{s}/api/v1"
    return s


def _daemon_oauth_login(
    *,
    profile: str,
    server: str,
    principal: str,
    name: str | None,
    role: str,
    capabilities: list[str],
    workspace_id: str | None,
    json_mode: bool,
) -> dict:
    ensure_daemon()
    with daemon_client() as client:
        start = client.post(
            "/v1/login",
            json={
                "profile": profile,
                "server": server,
                "principal": principal,
                "name": name,
                "role": role,
                "capabilities": capabilities,
                "workspace_id": workspace_id,
            },
        )
        if start.status_code >= 400:
            detail = start.json().get("detail") if start.headers.get("content-type", "").startswith(
                "application/json"
            ) else start.text
            raise ClientError(
                str(detail) or "login failed",
                code=ErrorCode.UNAUTHENTICATED,
                exit_code=EXIT_AUTH,
                http_status=start.status_code,
            )
        body = start.json()
        login_id = body["login_id"]
        user_code = body["user_code"]
        verify_url = body.get("verification_uri") or ""
        complete_url = body.get("verification_uri_complete") or verify_url
        interval = max(int(body.get("interval") or 2), 1)
        expires_in = int(body.get("expires_in") or 600)

        if not json_mode:
            console.print(
                Panel(
                    (
                        f"[bold]1. Visit the approval page:[/bold]\n"
                        f"   [cyan underline]{complete_url}[/cyan underline]\n\n"
                        f"[bold]2. Or enter this code on {verify_url}:[/bold]\n"
                        f"   [bold yellow] {user_code} [/bold yellow]\n\n"
                        f"[dim]Expires in {expires_in // 60} minutes.[/dim]"
                    ),
                    title="[bold cyan]AK5 Device Authorization[/bold cyan]",
                    border_style="cyan",
                )
            )

        deadline = time.time() + expires_in
        status_ctx = (
            console.status("[bold green]Waiting for authorization...[/bold green]", spinner="dots")
            if not json_mode
            else suppress()
        )
        with status_ctx:
            while True:
                if time.time() > deadline:
                    raise ClientError(
                        "Authorization timed out",
                        code=ErrorCode.UNAUTHENTICATED,
                        exit_code=EXIT_AUTH,
                    )
                time.sleep(interval)
                poll = client.get(f"/v1/login/{login_id}")
                if poll.status_code == 410:
                    raise ClientError(
                        "Authorization code expired",
                        code=ErrorCode.UNAUTHENTICATED,
                        exit_code=EXIT_AUTH,
                        http_status=410,
                    )
                if poll.status_code >= 400:
                    detail = (
                        poll.json().get("detail")
                        if poll.headers.get("content-type", "").startswith("application/json")
                        else poll.text
                    )
                    raise ClientError(
                        str(detail) or "authorization failed",
                        code=ErrorCode.UNAUTHENTICATED,
                        exit_code=EXIT_AUTH,
                        http_status=poll.status_code,
                    )
                result = poll.json()
                if result.get("status") == "approved":
                    return result
                # pending — continue


def _legacy_identify_login(
    *,
    api_url: str,
    actor_id: str,
    role: str,
    caps: str,
    actor_type: str,
    board: str | None,
    project_root,
    json_mode: bool,
) -> None:
    """Dev insecure identify path (identity.md §2) — kept for local loops."""
    capabilities = [c.strip() for c in caps.split(",") if c.strip()]
    payload = {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "name": actor_id.replace("-", " ").replace("_", " ").title(),
        "role": role,
        "capabilities": capabilities,
    }
    from ak5.security import identify_headers_from_env

    with open_client(require_auth=False, api_url=api_url) as client:
        data = client.identify(payload, extra_headers=identify_headers_from_env())
    token = data["access_token"]
    if board:
        with suppress(Exception), httpx.Client(timeout=10.0) as http:
            http.post(
                f"{api_url}/boards/{board}/members",
                json={"actor_id": actor_id, "role": role},
                headers={"Authorization": f"Bearer {token}"},
            )
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
    out = {
        "actor_id": actor_id,
        "role": role,
        "actor_type": actor_type,
        "session": str(session_path),
        "identity": str(claim_path),
        "source": "identify",
    }
    if json_mode:
        print_result(out, json_mode=True)
    else:
        console.print(
            f"[bold green]✓[/bold green] Authenticated as [cyan]{actor_id}[/cyan] [{actor_type}]"
        )
        console.print(f"[dim]Session:[/dim]  {session_path}")


@click.command("login")
@click.argument("url_arg", required=False, default=None)
@click.option("--agent", "agent_name", default=None, help="Agent display name (OAuth device grant)")
@click.option("--profile", default=None, help="Daemon credential profile name")
@click.option("--workspace", "workspace_id", default=None, help="Workspace id for the grant")
@click.option(
    "--id",
    "actor_id",
    default=None,
    help="[legacy] Stable actor id WITHOUT leading '@'. '@' is stripped if present.",
)
@click.option(
    "--role",
    default=None,
    type=click.Choice(_ROLE_CHOICES, case_sensitive=False),
    help="Workflow role: triage | worker | gatekeeper | human",
)
@click.option("--caps", default="", help="Comma-separated capabilities")
@click.option(
    "--type",
    "actor_type",
    type=click.Choice(["human", "agent"]),
    default="agent",
    help="[legacy] Actor type for identify",
)
@click.option("--board", default=None, help="[legacy] Board to enroll after identify")
@click.option("--url", default=None, help="AK5 Gateway API URL")
@click.option(
    "--device/--no-device",
    default=None,
    help="[deprecated] Legacy /auth/device — prefer default OAuth via --agent / --profile",
)
@click.option(
    "--user",
    "as_user",
    is_flag=True,
    help="User principal device grant (default when --agent is omitted and --profile is set)",
)
@click.pass_context
def login_command(
    ctx: click.Context,
    url_arg: str | None,
    agent_name: str | None,
    profile: str | None,
    workspace_id: str | None,
    actor_id: str | None,
    role: str | None,
    caps: str,
    actor_type: str,
    board: str | None,
    url: str | None,
    device: bool | None,
    as_user: bool,
) -> None:
    """Authenticate via OAuth device grant (daemon credential store).

    Agent::

      ak5 login --agent worker-1 --role worker --caps py [--profile worker-1]

    User::

      ak5 login --profile me [--workspace ws_…]

    Legacy (dev insecure identify)::

      ak5 login --id <actor> --role worker --caps …
    """
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    api_url = _normalize_server(url or url_arg or get_api_url())
    project_root = find_project_root()
    capabilities = [c.strip() for c in caps.split(",") if c.strip()]

    try:
        # Prefer OAuth when --agent or --profile (without legacy --id).
        if agent_name or (profile and not actor_id):
            if agent_name and as_user:
                raise click.UsageError("Cannot combine --agent with --user")
            principal = "user" if (as_user or not agent_name) else "agent"
            if principal == "agent":
                if not role:
                    raise click.UsageError("--role is required with --agent")
                role = role.lower()
                if role not in _AGENT_ROLES:
                    raise click.UsageError(
                        f"--role for agents must be one of: {', '.join(_AGENT_ROLES)}"
                    )
            else:
                role = (role or Role.HUMAN.value).lower()

            profile_name = (profile or agent_name or "me").strip()
            result = _daemon_oauth_login(
                profile=profile_name,
                server=api_url,
                principal=principal,
                name=agent_name,
                role=role,
                capabilities=capabilities,
                workspace_id=workspace_id,
                json_mode=json_mode,
            )
            out = {
                "profile": profile_name,
                "principal": principal,
                "role": role,
                "actor_id": result.get("actor_id") or "",
                "actor_type": result.get("actor_type") or principal,
                "server": api_url,
                "source": "daemon",
            }
            if json_mode:
                print_result(out, json_mode=True)
            else:
                console.print(
                    f"[bold green]✓[/bold green] Logged in profile "
                    f"[cyan]{profile_name}[/cyan] ({principal})"
                )
                if out["actor_id"]:
                    console.print(f"[dim]Actor:[/dim] {out['actor_id']}")
                console.print(f"[dim]Server:[/dim] {api_url}")
            return

        # Legacy identify / deprecated device path.
        if not actor_id:
            raise click.UsageError(
                "Specify --agent <name> --role …, or --profile me, "
                "or legacy --id <actor> --role …"
            )
        if not role:
            raise click.UsageError("--role is required")
        actor_id = clean_actor_id(actor_id)
        if not actor_id:
            raise click.UsageError("--id must be a non-empty actor id")
        role = role.lower()

        existing = list_identity_hints(project_root)
        if len(existing) >= 1 and actor_id not in {h["actor_id"] for h in existing} and not json_mode:
            console.print("[dim]Other local identities already present:[/dim]")
            _print_identity_hints(project_root)

        if device:
            raise ClientError(
                "Legacy --device (/auth/device) is removed; use "
                "`ak5 login --agent …` or `ak5 login --profile me`",
                code=ErrorCode.UNAUTHENTICATED,
                exit_code=EXIT_AUTH,
            )

        _legacy_identify_login(
            api_url=api_url,
            actor_id=actor_id,
            role=role,
            caps=caps,
            actor_type=actor_type,
            board=board,
            project_root=project_root,
            json_mode=json_mode,
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except httpx.ConnectError:
        handle_client_error(
            ClientError(f"Failed to connect to AK5 Gateway at {api_url}", exit_code=1),
            json_mode=json_mode,
        )
    except Exception as e:
        handle_client_error(e, json_mode=json_mode)
