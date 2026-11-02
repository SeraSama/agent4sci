"""Tiny MCP server used by the tests (stdio transport)."""

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("echo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.tool()
def shout(text: str) -> str:
    """Upper-case a text."""
    return text.upper()


@mcp.tool()
def fail() -> str:
    """Always fails with a message meant for the caller."""
    raise ToolError("boom")


if __name__ == "__main__":
    mcp.run()
