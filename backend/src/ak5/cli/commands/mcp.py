import click


@click.command("mcp")
def mcp_command() -> None:
    """Run the AK5 MCP Server in Stdio mode for Claude Desktop, Cursor, or agent harnesses."""
    try:
        import mcp as _mcp  # noqa: F401
    except ImportError:
        raise SystemExit(
            "AK5 MCP dependencies are not installed.\n"
            "Install the mcp extra and retry:\n"
            "  pip install 'ak5[mcp]'\n"
            "  # or: uv sync --extra mcp --all-packages"
        ) from None
    from ak5.mcp.server import main as run_mcp

    run_mcp()
