"""UI backend: a thin web layer that forwards calls to the MCP server.

Run: uv run uvicorn ui.backend:app --host 127.0.0.1 --port 8000
"""

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from config import load_config
from mcp_connection import error_text
from ui.session import McpSession, McpUnavailable

PAGE = Path(__file__).with_name("index.html")


class ToolCall(BaseModel):
    arguments: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run the MCP session worker for as long as the web server runs."""
    app.state.mcp = McpSession()
    worker = asyncio.create_task(app.state.mcp.run_worker())
    yield
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker


app = FastAPI(title="Private Credit MCP Tester", lifespan=lifespan)
# Refuse requests addressed to any other host name, so other websites cannot reach this page's API.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.exception_handler(McpUnavailable)
async def mcp_unavailable(_: Request, error: McpUnavailable) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=502)


@app.get("/")
async def page() -> FileResponse:
    return FileResponse(PAGE)


@app.get("/api/mode")
async def mode() -> dict[str, str]:
    return {"data_mode": load_config().data_mode}


@app.get("/api/tools")
async def list_tools() -> list[dict[str, Any]]:
    listing = await app.state.mcp.call(lambda client: client.list_tools())
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in listing.tools
    ]


@app.post("/api/tools/{name}")
async def call_tool(name: str, call: ToolCall) -> dict[str, Any]:
    known = sorted(tool["name"] for tool in await list_tools())
    if name not in known:
        raise HTTPException(404, f"Unknown tool '{name}'. Valid tools: {', '.join(known)}.")
    result = await app.state.mcp.call(lambda client: client.call_tool(name, call.arguments))
    return {
        "is_error": bool(result.is_error),
        "result": result.structured_content,
        "text": error_text(result),
    }
