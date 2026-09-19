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
    console.print(f"[bold cyan]🚀 Starting AK5 Gateway on http://{host}:{port}...[/bold cyan]")
    console.print(f"  - REST API & Swagger: [link=http://{host}:{port}/docs]http://{host}:{port}/docs[/link]")
    console.print(f"  - Embedded MCP SSE:   [link=http://{host}:{port}/mcp/sse]http://{host}:{port}/mcp/sse[/link]")
    console.print(f"  - SSE Real-time Bus:  [link=http://{host}:{port}/api/v1/events/stream]http://{host}:{port}/api/v1/events/stream[/link]\n")
    uvicorn.run("ak5.main:app", host=host, port=port, reload=reload)
