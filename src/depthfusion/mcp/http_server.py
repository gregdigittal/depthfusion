"""DepthFusion MCP HTTP/SSE server — JSON-RPC 2.0 over Server-Sent Events.

Transport: two-endpoint SSE pattern (MCP spec 2025-03-26)
  GET  /sse             → long-lived SSE stream; sends endpoint event + 30s pings
  POST /messages        → receives JSON-RPC body, dispatches via _process_request,
                          pushes response onto the session SSE queue
  GET  /health          → unauthenticated health probe

Security:
  - Default bind: 127.0.0.1:7301 (loopback)
  - DEPTHFUSION_MCP_PUBLIC=1 → binds 0.0.0.0 (requires DEPTHFUSION_MCP_TOKEN)
  - Bearer token validated on every /sse and /messages request
  - startup raises ValueError if public bind requested without token
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import AsyncGenerator, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from depthfusion.core.config import DepthFusionConfig
from depthfusion.mcp.server import _process_request

logger = logging.getLogger(__name__)

app = FastAPI(title="DepthFusion MCP HTTP/SSE", version="1.0.0")

_MCP_SESSIONS: dict[str, asyncio.Queue] = {}

_PING_INTERVAL = 30.0


# ---------------------------------------------------------------------------
# Auth + binding helpers (mirrors api/rest.py pattern exactly)
# ---------------------------------------------------------------------------

def get_mcp_bind_host() -> str:
    if os.getenv("DEPTHFUSION_MCP_PUBLIC", "0") == "1":
        return "0.0.0.0"  # noqa: S104 — guarded by validate_mcp_public_bind
    return "127.0.0.1"


def validate_mcp_public_bind() -> None:
    if os.getenv("DEPTHFUSION_MCP_PUBLIC", "0") == "1" and not os.getenv(
        "DEPTHFUSION_MCP_TOKEN", ""
    ):
        raise ValueError(
            "DEPTHFUSION_MCP_TOKEN must be set when DEPTHFUSION_MCP_PUBLIC=1. "
            "Public bind without bearer token authentication is forbidden."
        )


def _check_mcp_auth(authorization: Optional[str] = Header(default=None)) -> None:
    token = os.getenv("DEPTHFUSION_MCP_TOKEN", "")
    if os.getenv("DEPTHFUSION_MCP_PUBLIC", "0") == "1" and token:
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "transport": "sse", "version": "1.0.0"}


@app.get("/sse")
async def sse_endpoint(
    request: Request,
    _auth: None = Depends(_check_mcp_auth),
) -> StreamingResponse:
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _MCP_SESSIONS[session_id] = queue
    logger.info("MCP SSE session opened: %s", session_id)

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            # MCP spec: server immediately sends endpoint URI
            yield f"event: endpoint\ndata: /messages?sessionId={session_id}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=_PING_INTERVAL)
                    yield f"event: message\ndata: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _MCP_SESSIONS.pop(session_id, None)
            logger.info("MCP SSE session closed: %s", session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/messages")
async def messages_endpoint(
    request: Request,
    sessionId: str,
    _auth: None = Depends(_check_mcp_auth),
):
    if sessionId not in _MCP_SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    body = await request.json()
    config = DepthFusionConfig.from_env()

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None, _process_request, body, config
    )

    if response:
        await _MCP_SESSIONS[sessionId].put(json.dumps(response))

    return {"ok": True}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the MCP HTTP/SSE server.

    Reads all configuration from environment variables. Raises ValueError at
    startup if DEPTHFUSION_MCP_PUBLIC=1 without DEPTHFUSION_MCP_TOKEN.
    """
    validate_mcp_public_bind()

    host = get_mcp_bind_host()
    port = int(os.getenv("DEPTHFUSION_MCP_PORT", "7301"))

    logger.info(
        "DepthFusion MCP HTTP/SSE server starting on %s:%d", host, port
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
