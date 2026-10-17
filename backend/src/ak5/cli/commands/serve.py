"""``ak5 serve`` — unified server launcher (cli.md §4.1)."""

from __future__ import annotations

import contextlib
import ipaddress
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import click
import uvicorn
from rich.console import Console
from rich.panel import Panel

console = Console()

# Loopback / unspecified hosts that are safe without auth (cli.md §4.1).
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback_bind(host: str) -> bool:
    """Return True when ``host`` only accepts local connections."""
    normalized = host.strip().lower()
    if normalized in _LOOPBACK_NAMES:
        return True
    # Strip IPv6 brackets: [::1]
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def auth_is_configured() -> bool:
    """True when web auth and/or identify secret gates are active."""
    from ak5.security import get_identify_secret
    from ak5.web_auth import get_web_auth_config

    return get_web_auth_config().enabled or bool(get_identify_secret())


def assert_safe_bind(host: str, *, insecure: bool) -> None:
    """Refuse non-loopback bind when auth is off unless ``--insecure``."""
    if is_loopback_bind(host):
        return
    if auth_is_configured():
        return
    if insecure:
        console.print(
            "[bold yellow]Warning:[/bold yellow] Binding to "
            f"[cyan]{host}[/cyan] with authentication disabled "
            "([bold]--insecure[/bold])."
        )
        return
    console.print(
        f"[bold red]✗ Refusing to bind to {host} without authentication.[/bold red]\n"
        "  Non-loopback binds require [cyan]AK5_AUTH_PASSWORD[/cyan] and/or "
        "[cyan]AK5_IDENTIFY_SECRET[/cyan],\n"
        "  or pass [bold]--insecure[/bold] to override (not recommended)."
    )
    raise SystemExit(1)


def default_daemon_command() -> list[str]:
    """Argv for the sibling daemon process (``ak5 daemon run`` — issue #13)."""
    return [sys.executable, "-m", "ak5.cli.main", "daemon", "run"]


def start_daemon_sibling(
    command: list[str] | None = None,
) -> subprocess.Popen[str]:
    """Spawn daemon as a sibling process (events.md §4.5). Not in app lifespan."""
    argv = list(command) if command is not None else default_daemon_command()
    console.print(f"[cyan]▶ Starting sibling daemon:[/cyan] {' '.join(argv)}")
    return subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_daemon_sibling(
    proc: subprocess.Popen[str] | None,
    *,
    grace_seconds: float = 5.0,
) -> None:
    """Terminate a sibling daemon process started by ``serve --daemon``."""
    if proc is None:
        return
    if proc.poll() is not None:
        return
    console.print("[dim]Stopping sibling daemon...[/dim]")
    try:
        # start_new_session=True → kill the process group when possible.
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2.0)


def _load_dotenv_files() -> None:
    """Best-effort .env load for AK5_AUTH_* without adding python-dotenv."""
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / "frontend" / ".env",
    ]
    # Editable install: .../backend/src/ak5/cli/commands/serve.py → repo root parents[5]
    here = Path(__file__).resolve()
    if len(here.parents) >= 6:
        candidates.append(here.parents[5] / ".env")

    for path in candidates:
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue
        break


def _print_api_banner(host: str, port: int) -> None:
    from ak5.config import settings
    from ak5.web_ui_static import web_ui_available

    console.print(f"[bold cyan]Starting AK5 Gateway on http://{host}:{port}...[/bold cyan]")
    console.print(f"  - Database:           [cyan]{settings.DATABASE_URL}[/cyan]")
    console.print(
        f"  - REST API & Swagger: [link=http://{host}:{port}/docs]"
        f"http://{host}:{port}/docs[/link]"
    )
    console.print(
        f"  - Embedded MCP SSE:   [link=http://{host}:{port}/mcp/sse]"
        f"http://{host}:{port}/mcp/sse[/link]"
    )
    console.print(
        f"  - SSE Real-time Bus:  [link=http://{host}:{port}/api/v1/events/stream]"
        f"http://{host}:{port}/api/v1/events/stream[/link]"
    )
    if web_ui_available():
        console.print(
            f"  - Web Dashboard:      [link=http://{host}:{port}/]"
            f"http://{host}:{port}/[/link]"
        )
        console.print("  - Tip: use [bold cyan]ak5 serve --ui[/bold cyan] for Tailscale banner + browser\n")
    else:
        console.print("  - Web Dashboard:      [dim]not packaged (run scripts/build_web_ui.sh)[/dim]\n")


def _print_ui_banner(host: str, port: int, *, is_ssl: bool) -> None:
    from ak5.cli.tailscale import detect_tailscale
    from ak5.config import settings
    from ak5.security import get_identify_secret
    from ak5.web_auth import get_web_auth_config

    ts = detect_tailscale(port=port)
    cfg = get_web_auth_config()
    scheme = "https" if is_ssl else "http"

    local = f"{scheme}://127.0.0.1:{port}"
    lines = [
        f"[bold]Local:[/bold]              [cyan]{local}[/cyan]",
        f"[bold]API Docs:[/bold]           [cyan]{local}/docs[/cyan]",
        f"[bold]Database:[/bold]           [cyan]{settings.DATABASE_URL}[/cyan]",
    ]
    if ts.https_active and ts.https_url:
        lines.append(f"[bold]Tailscale HTTPS:[/bold]   [green]{ts.https_url}[/green] (secure port 443)")
    elif ts.dns_name:
        lines.append(f"[bold]Tailscale Domain:[/bold]  [cyan]{scheme}://{ts.dns_name}:{port}[/cyan]")
    if ts.ipv4:
        lines.append(f"[bold]Tailscale IP:[/bold]      [cyan]{scheme}://{ts.ipv4}:{port}[/cyan]")
    if host not in {"127.0.0.1", "localhost"}:
        lines.append(f"[bold]Bind:[/bold]               [cyan]{scheme}://{host}:{port}[/cyan]")

    if cfg.enabled:
        lines.append(f"[bold]Web Auth:[/bold]           [green]ENABLED[/green] (user: {cfg.username})")
        lines.append("[bold]Brute-force Shield:[/bold] [green]ACTIVE[/green] (5 / 5m, 10m lockout)")
    else:
        lines.append("[bold]Web Auth:[/bold]           [yellow]DISABLED[/yellow] (set AK5_AUTH_PASSWORD)")

    if get_identify_secret():
        lines.append("[bold]Identify Gate:[/bold]       [green]ENABLED[/green] (AK5_IDENTIFY_SECRET)")
        if not cfg.enabled:
            lines.append(
                "[bold]Note:[/bold]                Dashboard writes need web auth cookie "
                "or agents must share AK5_IDENTIFY_SECRET"
            )
    else:
        lines.append(
            "[bold]Identify Gate:[/bold]       [yellow]OPEN[/yellow] "
            "(set AK5_IDENTIFY_SECRET when exposing the API)"
        )

    console.print(
        Panel(
            "\n".join(lines),
            title="AK5 Kanban Web Dashboard",
            border_style="green",
        )
    )


def _open_browser_later(url: str, delay: float = 1.2) -> None:
    def _open() -> None:
        time.sleep(delay)
        with contextlib.suppress(Exception):
            webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def _prepare_database() -> None:
    from ak5.config import settings
    from ak5.paths import DatabaseConflictError, check_legacy_cwd_db
    from ak5.services.db_safety import DatabaseWipeError, prepare_database_for_use

    try:
        prepare_database_for_use(settings.DATABASE_URL)
    except (DatabaseConflictError, DatabaseWipeError) as exc:
        console.print(f"[bold red]✗ Database conflict[/bold red]\n{exc}")
        raise SystemExit(1) from exc
    if check_legacy_cwd_db():
        console.print(
            "[bold red]✗ Local ./ak5.db still present after reconcile.[/bold red] "
            "Run [cyan]ak5 migrate-legacy --force[/cyan] or delete ./ak5.db."
        )
        raise SystemExit(1)


def run_serve(
    *,
    host: str,
    port: int,
    ui: bool,
    daemon: bool,
    tailscale_serve: bool,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
    reload: bool,
    insecure: bool,
    no_browser: bool,
    daemon_command: list[str] | None = None,
) -> None:
    """Shared entry for ``ak5 serve`` and the deprecated ``ak5 web`` alias."""
    _load_dotenv_files()
    assert_safe_bind(host, insecure=insecure)
    _prepare_database()

    if ui:
        from ak5.web_ui_static import web_ui_available

        if not web_ui_available():
            console.print(
                "[bold yellow]Warning:[/bold yellow] Embedded web UI assets not found "
                "([cyan]ak5/web_ui/index.html[/cyan])."
            )
            console.print(
                "Build them with [bold cyan]scripts/build_web_ui.sh[/bold cyan] "
                "(CI/release does this automatically), then reinstall the package."
            )
            console.print("API-only mode will still start; open [cyan]/docs[/cyan] for Swagger.\n")

    if tailscale_serve:
        from ak5.cli.tailscale import enable_tailscale_serve

        console.print(f"[cyan]▶ Setting up Tailscale HTTPS proxy (tailscale serve --bg {port})...[/cyan]")
        ok, msg = enable_tailscale_serve(port)
        if not ok:
            console.print(f"[yellow]⚠️ Tailscale serve returned:[/yellow] {msg}")
        else:
            console.print("[green]✓ Tailscale HTTPS serve active on port 443[/green]")

    is_ssl = bool(ssl_keyfile and ssl_certfile)
    if ui:
        _print_ui_banner(host, port, is_ssl=is_ssl)
        if not no_browser:
            scheme = "https" if is_ssl else "http"
            _open_browser_later(f"{scheme}://127.0.0.1:{port}/")
        console.print("[dim]Press Ctrl+C to stop.[/dim]\n")
    else:
        _print_api_banner(host, port)

    daemon_proc: subprocess.Popen[str] | None = None
    use_proxy = ui or is_ssl or tailscale_serve
    try:
        if daemon:
            daemon_proc = start_daemon_sibling(daemon_command)
        uvicorn.run(
            "ak5.main:app",
            host=host,
            port=port,
            reload=reload,
            proxy_headers=use_proxy,
            forwarded_allow_ips="*" if use_proxy else None,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        )
    finally:
        stop_daemon_sibling(daemon_proc)


@click.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host interface to bind")
@click.option("--port", default=8000, show_default=True, type=int, help="Port to bind")
@click.option("--ui", is_flag=True, help="Serve embedded web dashboard (banner + optional browser)")
@click.option(
    "--daemon",
    is_flag=True,
    help="Also start `ak5 daemon run` as a sibling process (cleaned up on exit)",
)
@click.option("--tailscale-serve", is_flag=True, help="Proxy via Tailscale Serve (HTTPS on port 443)")
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
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option(
    "--insecure",
    is_flag=True,
    help="Allow non-loopback bind without auth (dangerous; not recommended)",
)
@click.option("--no-browser", is_flag=True, help="Do not open a browser tab (with --ui)")
def serve_command(
    host: str,
    port: int,
    ui: bool,
    daemon: bool,
    tailscale_serve: bool,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
    reload: bool,
    insecure: bool,
    no_browser: bool,
) -> None:
    """Start the AK5 Gateway REST API (and optionally the embedded web UI / daemon)."""
    run_serve(
        host=host,
        port=port,
        ui=ui,
        daemon=daemon,
        tailscale_serve=tailscale_serve,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
        reload=reload,
        insecure=insecure,
        no_browser=no_browser,
    )
