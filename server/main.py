"""MCP server entry point (stdio): uv run python -m server.main

Standard output is the MCP channel, so all logging goes to standard error.
"""

import logging
import sys

from mcp.server.mcpserver import MCPServer

from config import Config, load_config
from server.db import NO_DATA, NO_MOCK_DATA, Database
from server.resources import register_resources
from server.tools import register_tools

INSTRUCTIONS = (
    "Early-warning signs of borrower stress, from public BDC filings and FRED rates. Read "
    "card://stress-rules and card://data-sources first. Call list_bdcs when unsure what is loaded, "
    "and flag_stress_signals to build a watchlist. Cite the sources in each response."
)


def build_server(config: Config) -> MCPServer:
    """The server opens the database per request, so it starts without one."""
    mcp = MCPServer("private-credit", instructions=INSTRUCTIONS, log_level="WARNING")
    no_data = NO_MOCK_DATA if config.data_mode == "mock" else NO_DATA
    database = Database(config.paths.database, config.server.db_open_timeout_seconds, no_data)
    register_tools(mcp, config, database)
    register_resources(mcp, config, database)
    return mcp


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    build_server(load_config()).run("stdio")


if __name__ == "__main__":
    main()
