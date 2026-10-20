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
    """True when identity.md §8 auth is active for exposed binds.

    Non-loopback / Tailscale Serve require at least one OIDC provider.
    Legacy web password or identify secret alone are not sufficient.
    """
    from ak5.server.auth.oidc_config import oidc_providers_configured

    return oidc_providers_configured()


def warn_co_located_agent_env() -> None:
    """Warn when serve runs in an agent-marked environment (identity.md §8.2)."""
    markers = (
        "AK5_AGENT",
        "AK5_AGENT_ID",
        "AK5_AGENT_ROLE",
        "CURSOR_AGENT",
        "CLAUDE_AGENT",
    )
    hit = [name for name in markers if os.environ.get(name, "").strip()]
    if not hit:
        return
    console.print(
        "[bold yellow]Warning:[/bold yellow] Agent environment detected "
        f"({', '.join(hit)}). On local hosts, agents that share the server OS "
        "user can read JWT secrets and the DB under [cyan]AK5_DATA_DIR[/cyan] — "
        "identity checks cannot stop that (identity.md §8.2). Run agents as a "
        "different OS user/container, or move the server off-box."
    )


def assert_safe_bind(
    host: str,
    *,
    insecure: bool,
    tailscale_serve: bool = False,
) -> None:
    """Refuse public exposure when auth is off unless ``--insecure``.

    Exposure includes non-loopback binds **and** ``--tailscale-serve`` (which
    publishes the loopback-bound server to the tailnet over HTTPS).
    """
    exposed = (not is_loopback_bind(host)) or tailscale_serve
    if not exposed:
        return
    from ak5.server.config import hosted_profile
    from ak5.server.web_auth import hosted_client_ip_config_ok

    if hosted_profile() and not hosted_client_ip_config_ok():
        # Without it every request would share the proxy's IP (or let callers pick their own
        # bucket), which makes per-IP limits meaningless (operations.md §7 / #125).
        console.print(
            "[bold red]✗ Refusing AK5_DEPLOY_PROFILE=hosted on an exposed bind without "
            "a client-IP trust strategy.[/bold red]\n"
            "  Set [cyan]AK5_TRUSTED_PROXIES[/cyan] (CIDR list) or "
            "[cyan]AK5_CLIENT_IP_HEADER[/cyan]\n"
            "  (e.g. [cyan]x-real-ip[/cyan] / [cyan]x-vercel-forwarded-for[/cyan] on "
            "platform edges; operations.md §2.1),\n"
            "  or unset AK5_DEPLOY_PROFILE."
        )
        raise SystemExit(1)
    if auth_is_configured():
        return
    if insecure:
        reason = (
            f"Tailscale Serve on [cyan]{host}[/cyan]"
            if tailscale_serve and is_loopback_bind(host)
            else f"Binding to [cyan]{host}[/cyan]"
        )
        if tailscale_serve and not is_loopback_bind(host):
            reason = f"Binding to [cyan]{host}[/cyan] with Tailscale Serve"
        console.print(
            f"[bold yellow]Warning:[/bold yellow] {reason} with authentication "
            "disabled ([bold]--insecure[/bold])."
        )
        return
    if tailscale_serve and is_loopback_bind(host):
        console.print(
            "[bold red]✗ Refusing --tailscale-serve without authentication.[/bold red]\n"
            "  Tailscale Serve publishes this process to the tailnet over HTTPS.\n"
            "  Set [cyan]AK5_OIDC_PROVIDERS[/cyan] (identity.md §8),\n"
            "  or pass [bold]--insecure[/bold] to override (not recommended)."
        )
    else:
        console.print(
            f"[bold red]✗ Refusing to bind to {host} without OIDC.[/bold red]\n"
            "  Non-loopback binds require [cyan]AK5_OIDC_PROVIDERS[/cyan] "
            "(identity.md §8),\n"
            "  or pass [bold]--insecure[/bold] to override (not recommended).\n"
            "  Local owner password login remains loopback-only."
        )
    raise SystemExit(1)


_DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"


def trusted_proxy_ips() -> str:
    """Comma-separated proxy IPs trusted for ``X-Forwarded-*`` rewriting.

    Always includes loopback (Tailscale Serve / local reverse proxies). Extra
    entries come only from ``AK5_TRUSTED_PROXIES``.
    """
    extra = os.environ.get("AK5_TRUSTED_PROXIES", "").strip()
    if not extra:
        return _DEFAULT_TRUSTED_PROXIES
    extras = [part.strip() for part in extra.split(",") if part.strip()]
    if not extras:
        return _DEFAULT_TRUSTED_PROXIES
    return ",".join([_DEFAULT_TRUSTED_PROXIES, *extras])


def resolve_uvicorn_proxy_settings(
    host: str,
    *,
    ui: bool,
    is_ssl: bool,
    tailscale_serve: bool,
) -> tuple[bool, str | None]:
    """Return ``(proxy_headers, forwarded_allow_ips)`` for ``uvicorn.run``.

    Never uses ``forwarded_allow_ips="*"`` — that lets any client spoof
    ``X-Forwarded-For`` and bypass IP-based web-auth lockouts.

    - Loopback + (ui | ssl | tailscale): enable, trust loopback (+ env extras).
    - Non-loopback direct (no ``AK5_TRUSTED_PROXIES``): disable proxy headers.
    - Non-loopback with ``AK5_TRUSTED_PROXIES``: enable with the restricted list.
    """
    wants_proxy_features = ui or is_ssl or tailscale_serve
    loopback = is_loopback_bind(host)
    has_extra_trusted = bool(os.environ.get("AK5_TRUSTED_PROXIES", "").strip())

    if loopback and wants_proxy_features:
        return True, trusted_proxy_ips()
    if not loopback and wants_proxy_features and has_extra_trusted:
        return True, trusted_proxy_ips()
    return False, None


def default_daemon_command() -> list[str]:
    """Argv for the sibling daemon process (``ak5 daemon run`` — issue #13)."""
    return [sys.executable, "-m", "ak5.cli.main", "daemon", "run"]


def start_daemon_sibling(
    command: list[str] | None = None,
    *,
    early_exit_check_seconds: float = 1.0,
) -> subprocess.Popen[str]:
    """Spawn daemon as a sibling process (events.md §4.5). Not in app lifespan.

    stdout/stderr are appended to ``AK5_DATA_DIR/daemon/serve-daemon.log``. After
    start, poll briefly so an immediate crash surfaces a warning instead of
    silent failure.
    """
    from ak5.paths import get_app_data_dir

    argv = list(command) if command is not None else default_daemon_command()
    log_dir = get_app_data_dir() / "daemon"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "serve-daemon.log"

    console.print(f"[cyan]▶ Starting sibling daemon:[/cyan] {' '.join(argv)}")
    console.print(f"[dim]  daemon log: {log_path}[/dim]")

    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"\n--- ak5 serve --daemon start pid-pending argv={argv!r} ---\n")
        log_file.flush()
        proc = subprocess.Popen(
            argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    if early_exit_check_seconds > 0:
        time.sleep(early_exit_check_seconds)
    if proc.poll() is not None:
        console.print(
            "[bold yellow]Warning:[/bold yellow] Sibling daemon exited early "
            f"(code {proc.returncode}). See [cyan]{log_path}[/cyan]."
        )
    return proc


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
    """Best-effort .env load for AK5_AUTH_* without adding python-dotenv.

    Skips when ``AK5_SKIP_DOTENV`` is a truthy value (``1``/``true``/``yes``/``on``)
    so test suites can isolate from developer checkout ``.env`` files. Existing
    ``os.environ`` keys are never overwritten.
    """
    skip = os.environ.get("AK5_SKIP_DOTENV", "").strip().lower()
    if skip in {"1", "true", "yes", "on"}:
        return

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
    from ak5.server.config import settings
    from ak5.server.web_ui_static import web_ui_assets_built

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
    if web_ui_assets_built():
        console.print(
            f"  - Web Dashboard:      [link=http://{host}:{port}/]"
            f"http://{host}:{port}/[/link]"
        )
        console.print("  - Tip: use [bold cyan]ak5 serve --ui[/bold cyan] for Tailscale banner + browser\n")
    else:
        console.print("  - Web Dashboard:      [dim]not packaged (run scripts/build_web_ui.sh)[/dim]\n")


def _print_ui_banner(host: str, port: int, *, is_ssl: bool) -> None:
    from ak5.cli.tailscale import detect_tailscale
    from ak5.security import get_identify_secret
    from ak5.server.config import settings
    from ak5.server.web_auth import get_web_auth_config

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
    from ak5.paths import DatabaseConflictError, check_legacy_cwd_db
    from ak5.server.config import settings
    from ak5.server.services.db_safety import DatabaseWipeError, prepare_database_for_use

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


def assert_dev_insecure_identify_safe(
    host: str,
    *,
    dev_insecure_identify: bool,
    tailscale_serve: bool,
) -> None:
    """Refuse ``--dev-insecure-identify`` outside pure loopback (identity.md §2)."""
    if not dev_insecure_identify:
        return
    if tailscale_serve:
        console.print(
            "[bold red]✗ Refusing --dev-insecure-identify with --tailscale-serve.[/bold red]\n"
            "Insecure identify is loopback-only; Tailscale Serve exposes the API to the tailnet."
        )
        raise SystemExit(1)
    if not is_loopback_bind(host):
        console.print(
            "[bold red]✗ Refusing --dev-insecure-identify on non-loopback bind.[/bold red]\n"
            f"Host [cyan]{host}[/cyan] is not loopback; omit the flag or bind 127.0.0.1."
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
    dev_insecure_identify: bool = False,
) -> None:
    """Shared entry for ``ak5 serve`` and the deprecated ``ak5 web`` alias."""
    from ak5.cli.server_extra import require_server_extra

    require_server_extra()
    _load_dotenv_files()
    try:
        from ak5.server.config import get_deploy_profile

        get_deploy_profile()
    except ValueError as exc:
        console.print(f"[bold red]✗ {exc}[/bold red]")
        raise SystemExit(1) from exc
    assert_safe_bind(host, insecure=insecure, tailscale_serve=tailscale_serve)
    assert_dev_insecure_identify_safe(
        host, dev_insecure_identify=dev_insecure_identify, tailscale_serve=tailscale_serve
    )
    warn_co_located_agent_env()
    if dev_insecure_identify:
        os.environ["AK5_DEV_INSECURE_IDENTIFY"] = "1"
        console.print(
            "[yellow]AK5_DEV_INSECURE_IDENTIFY=1[/yellow] "
            "([cyan]--dev-insecure-identify[/cyan]; loopback only)."
        )
    _prepare_database()

    if ui:
        from ak5.server.web_ui_static import web_ui_assets_built

        if not web_ui_assets_built():
            console.print(
                "[bold yellow]Warning:[/bold yellow] Embedded web UI assets not found "
                "([cyan]ak5/server/web_ui/_next[/cyan])."
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
    proxy_headers, forwarded_allow_ips = resolve_uvicorn_proxy_settings(
        host, ui=ui, is_ssl=is_ssl, tailscale_serve=tailscale_serve
    )
    try:
        if daemon:
            daemon_proc = start_daemon_sibling(daemon_command)
        uvicorn.run(
            "ak5.server.app:app",
            host=host,
            port=port,
            reload=reload,
            proxy_headers=proxy_headers,
            forwarded_allow_ips=forwarded_allow_ips,
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
    help="Allow non-loopback bind or --tailscale-serve without auth (dangerous)",
)
@click.option(
    "--dev-insecure-identify",
    is_flag=True,
    help="Allow POST /auth/identify on loopback (identity.md §2); refuse with --tailscale-serve",
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
    dev_insecure_identify: bool,
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
        dev_insecure_identify=dev_insecure_identify,
    )
