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
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from ak5.contracts.stages import Role
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

_ROLE_CHOICES = [r.value for r in Role]


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


def _perform_device_flow(
    api_url: str,
    payload: dict,
    board_id: str | None,
    project_root,
    *,
    json_mode: bool,
) -> None:
    """Execute OAuth 2.0 Device Flow authorization with interactive terminal waiting."""
    code_req = {
        "actor_id": payload["actor_id"],
        "actor_type": payload["actor_type"],
        "name": payload["name"],
        "role": payload["role"],
        "capabilities": payload["capabilities"],
        "board_id": board_id,
    }

    with httpx.Client(timeout=15.0) as client:
        resp = client.post(f"{api_url}/auth/device/code", json=code_req)
        resp.raise_for_status()
        code_data = resp.json()

        device_code = code_data["device_code"]
        user_code = code_data["user_code"]
        verify_url = code_data["verification_uri"]
        complete_url = code_data["verification_uri_complete"]
        interval = max(code_data.get("interval", 3), 2)
        expires_in = code_data.get("expires_in", 600)

        if not json_mode:
            panel_content = (
                f"[bold]1. Visit the approval page:[/bold]\n"
                f"   [cyan underline]{complete_url}[/cyan underline]\n\n"
                f"[bold]2. Or enter this code manually on {verify_url}:[/bold]\n"
                f"   [bold yellow] {user_code} [/bold yellow]\n\n"
                f"[dim]Code expires in {expires_in // 60} minutes.[/dim]"
            )
            console.print(
                Panel(
                    panel_content,
                    title="[bold cyan]AK5 Agent Device Authorization[/bold cyan]",
                    border_style="cyan",
                )
            )

        start_time = time.time()
        status_ctx = (
            console.status("[bold green]Waiting for authorization...[/bold green]", spinner="dots")
            if not json_mode
            else suppress()
        )
        with status_ctx:
            while True:
                if time.time() - start_time > expires_in:
                    raise ClientError("Authorization timed out", exit_code=1)

                time.sleep(interval)
                poll_resp = client.post(
                    f"{api_url}/auth/device/token",
                    json={"device_code": device_code},
                )

                if poll_resp.status_code == 200:
                    token_data = poll_resp.json()
                    token = token_data["access_token"]
                    break

                if poll_resp.status_code == 403:
                    raise ClientError("Authorization denied by user", exit_code=1)

                error_detail = ""
                with suppress(Exception):
                    error_detail = poll_resp.json().get("detail", "")

                if "authorization_pending" in error_detail:
                    continue
                if "expired" in error_detail:
                    raise ClientError("Authorization code expired", exit_code=1)
                raise ClientError(f"Polling error: {poll_resp.text}", exit_code=1)

    session_path = save_session(
        {
            "token": token,
            "actor_id": payload["actor_id"],
            "actor_type": payload["actor_type"],
            "role": payload["role"],
            "api_url": api_url,
            "capabilities": payload["capabilities"],
        },
        project_root=project_root,
    )
    claim_path = identity_path_for(payload["actor_id"], project_root)
    out = {
        "actor_id": payload["actor_id"],
        "role": payload["role"],
        "actor_type": payload["actor_type"],
        "session": str(session_path),
        "identity": str(claim_path),
    }
    if json_mode:
        print_result(out, json_mode=True)
    else:
        console.print(
            f"[bold green]✓[/bold green] Authorized as [cyan]{payload['actor_id']}[/cyan] "
            f"[{payload['actor_type']}]"
        )
        console.print(f"[dim]Session:[/dim]  {session_path}")
        console.print(f"[dim]Identity:[/dim] {claim_path}")


@click.command("login")
@click.argument("url_arg", required=False, default=None)
@click.option(
    "--id",
    "actor_id",
    required=True,
    help="Stable actor id WITHOUT leading '@'. '@' is stripped if present.",
)
@click.option(
    "--role",
    required=True,
    type=click.Choice(_ROLE_CHOICES, case_sensitive=False),
    help="Workflow role: triage | worker | gatekeeper | human",
)
@click.option("--caps", default="", help="Comma-separated capabilities")
@click.option("--type", "actor_type", type=click.Choice(["human", "agent"]), default="agent")
@click.option("--board", default=None, help="Board ID to enroll this actor with")
@click.option("--url", default=None, help="AK5 Gateway API URL")
@click.option("--device/--no-device", default=None, help="Use browser Device Code Flow")
@click.pass_context
def login_command(
    ctx: click.Context,
    url_arg: str | None,
    actor_id: str,
    role: str,
    caps: str,
    actor_type: str,
    board: str | None,
    url: str | None,
    device: bool | None,
) -> None:
    """Identify and authenticate as an Actor (Human or AI Agent).

    Agent happy path:
      ak5 login --type agent --role worker --caps ..
      ak5 subscribe add <board> --events WORK_AVAILABLE --hook-exec '<wake>'
      ak5 daemon start
      # on wake: ak5 next --json → ak5 ticket claim → work → ak5 ticket submit
    """
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    actor_id = clean_actor_id(actor_id)
    if not actor_id:
        raise click.UsageError("--id must be a non-empty actor id (without leading @).")

    role = role.lower()
    api_url = url or url_arg or get_api_url()
    capabilities = [c.strip() for c in caps.split(",") if c.strip()]
    project_root = find_project_root()

    existing = list_identity_hints(project_root)
    if len(existing) >= 1 and actor_id not in {h["actor_id"] for h in existing} and not json_mode:
        console.print("[dim]Other local identities already present:[/dim]")
        _print_identity_hints(project_root)

    payload = {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "name": actor_id.replace("-", " ").replace("_", " ").title(),
        "role": role,
        "capabilities": capabilities,
    }

    use_device_flow = bool(device)

    try:
        if use_device_flow:
            # Version-check against server before device flow.
            with open_client(require_auth=False, api_url=api_url) as client:
                client.ensure_version()
            _perform_device_flow(api_url, payload, board, project_root, json_mode=json_mode)
            return

        from ak5.security import identify_headers_from_env

        with open_client(require_auth=False, api_url=api_url) as client:
            data = client.identify(payload, extra_headers=identify_headers_from_env())

        token = data["access_token"]

        if board:
            try:
                with httpx.Client(timeout=10.0) as http:
                    mem_resp = http.post(
                        f"{api_url}/boards/{board}/members",
                        json={"actor_id": actor_id, "role": role},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if mem_resp.status_code == 200 and not json_mode:
                        console.print(f"[dim]Enrolled in board:[/dim] [cyan]{board}[/cyan]")
            except Exception as ex:
                if not json_mode:
                    console.print(
                        f"[dim yellow]Notice: Could not enroll in board '{board}': {ex}[/dim yellow]"
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
        }
        if json_mode:
            print_result(out, json_mode=True)
        else:
            console.print(
                f"[bold green]✓[/bold green] Authenticated as [cyan]{actor_id}[/cyan] [{actor_type}]"
            )
            console.print(f"[dim]Session:[/dim]  {session_path}")
            console.print(f"[dim]Identity:[/dim] {claim_path}")
            console.print(
                "[dim]Next:[/dim] export AK5_ACTOR_ID="
                f"{actor_id}"
                " && ak5 whoami && ak5 next --json"
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
