"""ak5 next — peek at next claimable ticket without claiming (cli.md §4.3)."""

from __future__ import annotations

import time

import click
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.client.errors import EXIT_EMPTY, ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from rich.console import Console

console = Console()


@click.command("next")
@click.option("--board", "board_id", default=None, help="Board ID (default: inbox if --inbox)")
@click.option("--inbox", is_flag=True, help="Use the system inbox board")
@click.option("--caps", default="", help="Comma-separated capabilities filter")
@click.option(
    "--wait",
    "wait_seconds",
    default=None,
    type=float,
    help="If empty, wait up to N seconds for WORK_AVAILABLE then retry (client.md §4.4)",
)
@click.pass_context
def next_command(
    ctx: click.Context,
    board_id: str | None,
    inbox: bool,
    caps: str,
    wait_seconds: float | None,
) -> None:
    """Return the next claimable ticket (does not claim). Exit 6 if none."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    target = "inbox" if inbox or not board_id else board_id
    capabilities = [c.strip() for c in caps.split(",") if c.strip()] or None
    deadline = time.time() + wait_seconds if wait_seconds and wait_seconds > 0 else None

    try:
        while True:
            try:
                with open_client() as client:
                    ticket = client.next_ticket(target, capabilities=capabilities)
                if json_mode:
                    print_result(ticket, json_mode=True)
                else:
                    console.print(
                        f"[bold green]✓[/bold green] Next: [cyan]{ticket.get('ticket_id')}[/cyan] "
                        f"— {ticket.get('title')} (stage={ticket.get('stage')})"
                    )
                return
            except ClientError as exc:
                if exc.exit_code != EXIT_EMPTY or deadline is None:
                    raise
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise
                _wait_for_work(min(remaining, 25.0))
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


def _wait_for_work(timeout: float) -> None:
    """Block on daemon local events; fall back to sleep polling without daemon."""
    try:
        from ak5.daemon.ipc import daemon_client, ensure_daemon
        from ak5.daemon.server import is_daemon_running

        if is_daemon_running():
            with daemon_client(timeout=timeout + 5) as dclient:
                dclient.get(
                    "/v1/events",
                    params={"wait": timeout, "events": "WORK_AVAILABLE", "cursor": 0},
                )
            return
        # Autostart when wait was requested
        ensure_daemon()
        with daemon_client(timeout=timeout + 5) as dclient:
            dclient.get(
                "/v1/events",
                params={"wait": timeout, "events": "WORK_AVAILABLE", "cursor": 0},
            )
        return
    except Exception:
        pass
    # daemon unavailable — poll gap up to 5s (client.md §4.4)
    time.sleep(min(max(timeout, 0.0), 5.0))
