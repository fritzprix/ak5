import click
import uvicorn
from rich.console import Console

console = Console()


@click.command("serve")
@click.option("--host", default="127.0.0.1", help="Host interface to bind")
@click.option("--port", default=8000, type=int, help="Port to bind")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve_command(host: str, port: int, reload: bool) -> None:
    """Start the AK5 Gateway REST API & Embedded MCP Server."""
    from ak5.paths import DatabaseConflictError, check_legacy_cwd_db
    from ak5.services.db_safety import DatabaseWipeError, prepare_database_for_use
    from ak5.web_ui_static import web_ui_available

    console.print(f"[bold cyan]Starting AK5 Gateway on http://{host}:{port}...[/bold cyan]")
    # Import settings after dual-DB preflight where possible; URL SOU checked below.
    from ak5.config import settings

    try:
        prepare_database_for_use(settings.DATABASE_URL)
    except (DatabaseConflictError, DatabaseWipeError) as exc:
        console.print(f"[bold red]✗ Database conflict[/bold red]\n{exc}")
        raise SystemExit(1) from exc

    console.print(f"  - Database:           [cyan]{settings.DATABASE_URL}[/cyan]")
    if check_legacy_cwd_db():
        console.print(
            "  [bold yellow]! Local ./ak5.db still present after reconcile — "
            "refusing implicit dual-DB use.[/bold yellow]"
        )
        console.print("    [dim]Run 'ak5 migrate-legacy --force' or delete ./ak5.db[/dim]")
        raise SystemExit(1)
    console.print(f"  - REST API & Swagger: [link=http://{host}:{port}/docs]http://{host}:{port}/docs[/link]")
    console.print(f"  - Embedded MCP SSE:   [link=http://{host}:{port}/mcp/sse]http://{host}:{port}/mcp/sse[/link]")
    console.print(f"  - SSE Real-time Bus:  [link=http://{host}:{port}/api/v1/events/stream]http://{host}:{port}/api/v1/events/stream[/link]")
    if web_ui_available():
        console.print(f"  - Web Dashboard:      [link=http://{host}:{port}/]http://{host}:{port}/[/link]")
        console.print("  - Tip: use [bold cyan]ak5 web[/bold cyan] for Tailscale URL banner + browser open\n")
    else:
        console.print("  - Web Dashboard:      [dim]not packaged (run scripts/build_web_ui.sh)[/dim]\n")
    uvicorn.run("ak5.main:app", host=host, port=port, reload=reload)
