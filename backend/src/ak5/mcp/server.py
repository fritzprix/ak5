from ak5.mcp.tools import server


def main() -> None:
    """Run MCP server in stdio mode for Claude Desktop or autonomous agents."""
    server.run("stdio")


if __name__ == "__main__":
    main()
