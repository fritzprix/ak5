"""Pure-Python launcher for the embedded Kanban web dashboard (single port)."""

from __future__ import annotations

import contextlib
import os
import threading
import time
import webbrowser
from pathlib import Path

import click
import uvicorn
from ak5.cli.tailscale import detect_tailscale
from ak5.web_auth import get_web_auth_config
from ak5.web_ui_static import web_ui_available
from rich.console import Console
from rich.panel import Panel

console = Console()


def _load_dotenv_files() -> None:
    """Best-effort .env load for AK5_AUTH_* without adding python-dotenv."""
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / "frontend" / ".env",
    ]
    # Editable install: .../backend/src/ak5/cli/commands/web.py → repo root parents[5]
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


def _print_ready_banner(host: str, port: int, is_ssl: bool = False) -> None:
    ts = detect_tailscale(port=port)
    cfg = get_web_auth_config()
    scheme = "https" if is_ssl else "http"
    local = f"{scheme}://127.0.0.1:{port}"
    lines = [
        f"[bold]Local:[/bold]              [cyan]{local}[/cyan]",
        f"[bold]API Docs:[/bold]           [cyan]{local}/docs[/cyan]",
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


@click.command("web")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind address (0.0.0.0 for Tailscale/LAN)")
@click.option("--port", default=8000, show_default=True, type=int, help="Single port for API + dashboard")
@click.option("--ssl-keyfile", default=None, type=click.Path(exists=True), help="SSL private key file for direct HTTPS")
@click.option("--ssl-certfile", default=None, type=click.Path(exists=True), help="SSL certificate file for direct HTTPS")
@click.option("--tailscale-serve", is_flag=True, help="Proxy via Tailscale Serve (HTTPS on port 443)")
@click.option("--no-browser", is_flag=True, help="Do not open a browser tab")
@click.option("--reload", is_flag=True, help="Auto-reload (development)")
def web_command(
    host: str,
    port: int,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
    tailscale_serve: bool,
    no_browser: bool,
    reload: bool,
) -> None:
    """Start AK5 with the embedded Kanban Web Dashboard on one port.

    Serves the packaged static UI (when present) plus REST/SSE/MCP.
    Detects Tailscale IP / MagicDNS automatically when the Tailscale CLI exists.
    Optional login gate via AK5_AUTH_PASSWORD (see .env.example).
    """
    _load_dotenv_files()

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
        import shutil
        import subprocess

        if shutil.which("tailscale"):
            console.print(f"[cyan]▶ Setting up Tailscale HTTPS proxy (tailscale serve --bg {port})...[/cyan]")
            res = subprocess.run(["tailscale", "serve", "--bg", str(port)], capture_output=True, text=True, check=False)
            if res.returncode != 0:
                err_msg = res.stderr.strip() or res.stdout.strip()
                console.print(f"[yellow]⚠️ Tailscale serve returned:[/yellow] {err_msg}")
            else:
                console.print("[green]✓ Tailscale HTTPS serve active on port 443[/green]")

    is_ssl = bool(ssl_keyfile and ssl_certfile)
    _print_ready_banner(host, port, is_ssl=is_ssl)

    if not no_browser:
        scheme = "https" if is_ssl else "http"
        _open_browser_later(f"{scheme}://127.0.0.1:{port}/")

    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")
    uvicorn.run(
        "ak5.main:app",
        host=host,
        port=port,
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
