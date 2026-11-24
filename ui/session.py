"""Keeps one MCP session open for the web server and replaces it if the MCP server dies."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from mcp import Client

from mcp_connection import connect

REQUEST_TIMEOUT_SECONDS = 45
WAIT_TIMEOUT_SECONDS = 60  # includes time in the queue
Request = Callable[[Client], Awaitable[Any]]
logger = logging.getLogger(__name__)


class McpUnavailable(RuntimeError):
    """The MCP server did not answer."""


class McpSession:
    """One worker task owns the connection, because it must open and close in the same task."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[Request, asyncio.Future]] = asyncio.Queue()

    async def call(self, request: Request) -> Any:
        """Hand a request to the worker and wait for its answer."""
        answer = asyncio.get_running_loop().create_future()
        await self._queue.put((request, answer))
        try:
            return await asyncio.wait_for(answer, WAIT_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise McpUnavailable("The MCP server took too long to answer.") from error

    async def run_worker(self) -> None:
        """Connect when a request arrives. After a failure, the next request reconnects."""
        while True:
            request, answer = await self._queue.get()
            try:
                async with connect() as client:
                    while True:
                        if not answer.done():  # done means the caller gave up waiting
                            result = await asyncio.wait_for(request(client), REQUEST_TIMEOUT_SECONDS)
                            if not answer.done():
                                answer.set_result(result)
                        request, answer = await self._queue.get()
            except Exception:
                logger.exception("MCP session failed")
                if not answer.done():
                    answer.set_exception(McpUnavailable("The MCP server stopped. Run the tool again."))
