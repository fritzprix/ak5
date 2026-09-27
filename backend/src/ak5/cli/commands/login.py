import time
from contextlib import suppress

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
from rich.panel import Panel
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


def _perform_device_flow(
    api_url: str,
    payload: dict,
    board_id: str | None,
    project_root,
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
        # Request device code
        resp = client.post(f"{api_url}/auth/device/code", json=code_req)
        resp.raise_for_status()
        code_data = resp.json()

        device_code = code_data["device_code"]
        user_code = code_data["user_code"]
        verify_url = code_data["verification_uri"]
        complete_url = code_data["verification_uri_complete"]
        interval = max(code_data.get("interval", 3), 2)
        expires_in = code_data.get("expires_in", 600)

        # Print user instructions in a styled panel
        panel_content = (
            f"[bold]1. Visit the approval page:[/bold]\n"
            f"   [cyan underline]{complete_url}[/cyan underline]\n\n"
            f"[bold]2. Or enter this code manually on {verify_url}:[/bold]\n"
            f"   [bold yellow text_large] {user_code} [/bold yellow text_large]\n\n"
            f"[dim]Code expires in {expires_in // 60} minutes.[/dim]"
        )
        console.print(Panel(panel_content, title="[bold cyan]AK5 Agent Device Authorization[/bold cyan]", border_style="cyan"))

        # Poll for approval
        start_time = time.time()
        with console.status("[bold green]Waiting for authorization in browser...[/bold green]", spinner="dots"):
            while True:
                if time.time() - start_time > expires_in:
                    console.print("[bold red]✗ Authorization timed out.[/bold red]")
                    raise click.Abort()

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
                    console.print("[bold red]✗ Authorization denied by user.[/bold red]")
                    raise click.Abort()

                error_detail = ""
                with suppress(Exception):
                    error_detail = poll_resp.json().get("detail", "")

                if "authorization_pending" in error_detail:
                    continue

                if "expired" in error_detail:
                    console.print("[bold red]✗ Authorization code expired.[/bold red]")
                    raise click.Abort()

                # Unknown error
                console.print(f"[bold red]✗ Polling error:[/bold red] {poll_resp.text}")
                raise click.Abort()

    # Save session
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
    console.print(f"[bold green]✓[/bold green] Authorized as [cyan]{payload['actor_id']}[/cyan] [{payload['actor_type']}]")
    console.print(f"[dim]Session:[/dim]  {session_path}")
    console.print(f"[dim]Identity:[/dim] {claim_path}")


@click.command("login")
@click.option("--id", "actor_id", required=True, help="Actor ID (e.g. agent-code-reviewer, user_pm)")
@click.option("--role", required=True, help="Display role (e.g. 'Senior Reviewer', 'PM')")
@click.option("--caps", default="", help="Comma-separated capabilities (e.g. 'python,rust,security')")
@click.option("--type", "actor_type", type=click.Choice(["human", "agent"]), default="agent", help="Actor type")
@click.option("--board", default=None, help="Board ID to associate/enroll this agent with")
@click.option("--url", default=None, help="AK5 Gateway API URL")
@click.option("--device/--no-device", default=None, help="Use browser Device Code Flow authorization")
def login_command(
    actor_id: str,
    role: str,
    caps: str,
    actor_type: str,
    board: str | None,
    url: str | None,
    device: bool | None,
) -> None:
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

    # Only use device flow if explicitly opted in via --device
    use_device_flow = bool(device)

    try:
        if use_device_flow:
            _perform_device_flow(api_url, payload, board, project_root)
            return

        # Direct identify flow (automated / CI / default)
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

        # If --board specified, enroll actor in the board
        if board:
            try:
                with httpx.Client(timeout=10.0) as client:
                    mem_resp = client.post(
                        f"{api_url}/boards/{board}/members",
                        json={"actor_id": actor_id, "role": role},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if mem_resp.status_code == 200:
                        console.print(f"[dim]Enrolled in board:[/dim] [cyan]{board}[/cyan]")
                    else:
                        console.print(f"[dim yellow]Notice: Could not auto-enroll in board '{board}' ({mem_resp.status_code})[/dim yellow]")
            except Exception as ex:
                console.print(f"[dim yellow]Notice: Could not enroll in board '{board}': {ex}[/dim yellow]")

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
