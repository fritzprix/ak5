import click


@click.command("mcp")
def mcp_command() -> None:
    """Run the AK5 MCP Server in Stdio mode for Claude Desktop, Cursor, or agent harnesses."""
    from ak5.mcp.server import main as run_mcp
    run_mcp()
