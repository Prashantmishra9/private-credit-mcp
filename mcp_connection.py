"""Starts the MCP server over stdio. Shared by test_client.py and the UI backend."""

import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent


def connect() -> Client:
    """Use as `async with connect() as client:`. The server stops when the block ends."""
    return Client(StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.main"],
        cwd=ROOT,
        env=dict(os.environ),  # so keys set in the shell also reach the server
    ))


def error_text(result) -> str:
    """The readable text of a tool result."""
    return "\n".join(block.text for block in result.content if block.type == "text")
