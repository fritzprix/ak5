"""Deprecated alias: ``ak5 web`` → ``ak5 serve --ui`` (cli.md §4.1 / §5)."""

from __future__ import annotations

import click
from rich.console import Console

console = Console()


@click.command("web", deprecated=True)
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address")
@click.option("--port", default=8000, show_default=True, type=int, help="Single port for API + dashboard")
@click.option(
    "--ssl-keyfile",
    default=None,
    type=click.Path(exists=True),
    help="SSL private key file for direct HTTPS",
)
@click.option(
    "--ssl-certfile",
    default=None,
    type=click.Path(exists=True),
    help="SSL certificate file for direct HTTPS",
)
@click.option("--tailscale-serve", is_flag=True, help="Proxy via Tailscale Serve (HTTPS on port 443)")
@click.option("--no-browser", is_flag=True, help="Do not open a browser tab")
@click.option("--reload", is_flag=True, help="Auto-reload (development)")
@click.option(
    "--insecure",
    is_flag=True,
    help="Allow non-loopback bind without auth (dangerous; not recommended)",
)
@click.option(
    "--daemon",
    is_flag=True,
    help="Also start `ak5 daemon run` as a sibling process (cleaned up on exit)",
)
def web_command(
    host: str,
    port: int,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
    tailscale_serve: bool,
    no_browser: bool,
    reload: bool,
    insecure: bool,
    daemon: bool,
) -> None:
    """Deprecated alias for ``ak5 serve --ui``.

    Starts AK5 with the embedded Kanban Web Dashboard on one port.
    Prefer ``ak5 serve --ui`` going forward.
    """
    console.print(
        "[yellow]Deprecated:[/yellow] ``ak5 web`` is an alias for "
        "[bold cyan]ak5 serve --ui[/bold cyan]. Prefer the latter.\n"
    )
    from ak5.cli.commands.serve import run_serve

    run_serve(
        host=host,
        port=port,
        ui=True,
        daemon=daemon,
        tailscale_serve=tailscale_serve,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
        reload=reload,
        insecure=insecure,
        no_browser=no_browser,
        dev_insecure_identify=False,
    )
