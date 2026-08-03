"""DepthFusion MCP HTTP/SSE server — JSON-RPC 2.0 over Server-Sent Events.

Transport: two-endpoint SSE pattern (MCP spec 2024-11-05)
  GET  /sse             → long-lived SSE stream; sends endpoint event + 30s pings
  POST /messages        → receives JSON-RPC body, dispatches via _process_request,
                          pushes response onto the session SSE queue
  GET  /health          → unauthenticated health probe

Streamable HTTP transport (MCP spec 2025-03-26)
  POST /mcp             → JSON-RPC single-shot or session-bound requests;
                          initialize issues Mcp-Session-Id; notifications → 202
  GET  /mcp             → server-initiated text/event-stream keyed by session
  DELETE /mcp           → session teardown

Security:
  - Bind host: controlled by DEPTHFUSION_MCP_HOST (default 127.0.0.1 / loopback).
    Set to 0.0.0.0 only when exposing the server to remote clients AND auth is
    fully configured (see below).
  - Fail-closed auth: all /mcp, /sse, and /messages endpoints require a valid
    Bearer token via require_principal (DEPTHFUSION_V2_LEGACY_AUTH=1 +
    DEPTHFUSION_API_TOKEN for development; JWKS/OIDC in production).
    There is no pass-through mode.
  - Origin header validation (MCP 2025-03-26 §2.1): /mcp routes check the Origin
    header only when DEPTHFUSION_MCP_ALLOWED_ORIGINS is set. When unset the check
    is permissive (any Origin accepted). When set, only listed origins are accepted;
    unlisted origins receive 403. Absent Origin is always accepted.
  - /health is the only unauthenticated endpoint.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from importlib.metadata import version as _pkg_version
from typing import Any, AsyncGenerator, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from depthfusion.api.auth import require_principal
from depthfusion.capture.hygiene import build_scheduler  # S-248: nightly hygiene scheduler
from depthfusion.core.config import DepthFusionConfig
from depthfusion.identity.models import Principal
from depthfusion.mcp.server import _process_request

_VERSION = _pkg_version("depthfusion")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — starts/stops the hygiene scheduler (S-248 AC-1)
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[type-arg]  # noqa: ARG001
    """FastAPI lifespan context manager.

    Starts the APScheduler AsyncIOScheduler on app startup and shuts it down
    cleanly on app shutdown so no scheduler threads leak.  Failures to build
    or start the scheduler are logged and swallowed — the HTTP server must
    remain available even when the hygiene scheduler cannot initialise.
    """
    try:
        scheduler = build_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.warning("lifespan: could not build hygiene scheduler: %s", exc)
        scheduler = None

    if scheduler is not None:
        try:
            scheduler.start()
            logger.info("lifespan: hygiene scheduler started")
        except Exception as exc:  # noqa: BLE001
            logger.warning("lifespan: failed to start hygiene scheduler: %s", exc)
            scheduler = None

    try:
        yield
    finally:
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
                logger.info("lifespan: hygiene scheduler stopped")
            except Exception as exc:  # noqa: BLE001
                logger.warning("lifespan: error stopping hygiene scheduler: %s", exc)


app = FastAPI(title="DepthFusion MCP HTTP/SSE", version=_VERSION, lifespan=_lifespan)

_MCP_SESSIONS: dict[str, asyncio.Queue] = {}

# Streamable HTTP session registry (MCP spec 2025-03-26): keyed by the
# Mcp-Session-Id issued on initialize; each session owns an asyncio.Queue
# feeding the GET /mcp server-initiated event stream.
_MCP_STREAMABLE_SESSIONS: dict[str, asyncio.Queue] = {}

_SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2025-03-26"})
_DEFAULT_PROTOCOL_VERSION = "2025-03-26"

_PING_INTERVAL = 30.0

# ---------------------------------------------------------------------------
# Origin validation — MCP 2025-03-26 §2.1 DNS-rebinding guard
# ---------------------------------------------------------------------------


class _OriginForbidden(Exception):
    """Raised when a request's Origin header is not in the allowed list."""


@app.exception_handler(_OriginForbidden)
async def _origin_forbidden_handler(request: Request, exc: _OriginForbidden) -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": "Origin not allowed"})


async def _check_origin(request: Request) -> None:
    """Reject /mcp requests whose Origin header is not in the allowed list.

    Behavior:
    - Absent Origin header → always accepted (CLI tools, Claude Code).
    - DEPTHFUSION_MCP_ALLOWED_ORIGINS not set → permissive, no restriction.
    - DEPTHFUSION_MCP_ALLOWED_ORIGINS set → Origin must match the list; if
      the Origin header is present and not listed → 403 {"error": "Origin not allowed"}.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    raw = os.getenv("DEPTHFUSION_MCP_ALLOWED_ORIGINS", "")
    if not raw:
        # Env var not set → fully permissive
        return
    allowed = frozenset(o.strip().rstrip("/") for o in raw.split(",") if o.strip())
    if origin.rstrip("/") not in allowed:
        raise _OriginForbidden()


# ---------------------------------------------------------------------------
# Binding helpers
# ---------------------------------------------------------------------------

def get_mcp_bind_host() -> str:
    """Return the host to bind to.

    Reads DEPTHFUSION_MCP_HOST (default ``127.0.0.1``).  Set to ``0.0.0.0``
    only when remote access is required AND auth is fully configured.
    """
    return os.getenv("DEPTHFUSION_MCP_HOST", "127.0.0.1")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "transports": ["sse", "streamable-http"], "version": _VERSION}


@app.post("/mcp")
async def streamable_http_endpoint(
    request: Request,
    _principal: Principal = Depends(require_principal),
    _origin: None = Depends(_check_origin),
):
    """Streamable HTTP transport (MCP spec 2025-03-26) — required by Claude Code ≥2.1.x.

    - initialize → issues an Mcp-Session-Id header and registers the session.
    - Subsequent requests carrying Mcp-Session-Id route to that session.
    - JSON-RPC notifications (no id field) → HTTP 202 with empty body.
    - Accept negotiation: application/json (default) or text/event-stream
      response framing when the client only accepts SSE.
    - MCP-Protocol-Version header validated: 2025-03-26 accepted, absent
      header tolerated for back-compat, anything else → 400.
    """
    protocol_version = request.headers.get("mcp-protocol-version")
    if protocol_version is not None and protocol_version not in _SUPPORTED_PROTOCOL_VERSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported MCP-Protocol-Version: {protocol_version}",
        )

    content_type = request.headers.get("content-type", "")
    if content_type and "application/json" not in content_type.lower():
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/json",
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object, not an array or primitive",
        )

    session_id = request.headers.get("mcp-session-id")
    if session_id is not None and session_id not in _MCP_STREAMABLE_SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    config = DepthFusionConfig.from_env()
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _process_request, body, config)

    # JSON-RPC notifications (no id) → 202 Accepted, empty body.
    if "id" not in body:
        return Response(status_code=202)

    headers = {"MCP-Protocol-Version": _DEFAULT_PROTOCOL_VERSION}

    is_initialize = body.get("method") == "initialize"
    if is_initialize and session_id is None:
        session_id = str(uuid.uuid4())
        _MCP_STREAMABLE_SESSIONS[session_id] = asyncio.Queue()
        logger.info("MCP streamable-http session opened: %s", session_id)
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id

    accept = request.headers.get("accept", "")
    wants_sse = "text/event-stream" in accept and "application/json" not in accept
    if wants_sse:
        async def single_event() -> AsyncGenerator[str, None]:
            if response:
                yield f"event: message\ndata: {json.dumps(response)}\n\n"

        return StreamingResponse(
            single_event(),
            media_type="text/event-stream",
            headers={
                **headers,
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return JSONResponse(content=response or {}, headers=headers)


@app.get("/mcp")
async def streamable_http_sse_endpoint(
    request: Request,
    _principal: Principal = Depends(require_principal),
    _origin: None = Depends(_check_origin),
) -> StreamingResponse:
    """Server-initiated event stream for a streamable-http session (MCP 2025-03-26).

    Requires the Mcp-Session-Id header issued by initialize; streams queued
    server events with 30s keep-alive pings, mirroring the /sse transport.
    """
    session_id = request.headers.get("mcp-session-id")
    if not session_id:
        raise HTTPException(status_code=400, detail="Mcp-Session-Id header required")
    queue = _MCP_STREAMABLE_SESSIONS.get(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=_PING_INTERVAL)
                    yield f"event: message\ndata: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            logger.info("MCP streamable-http event stream closed: %s", session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/mcp")
async def streamable_http_delete_endpoint(
    request: Request,
    _principal: Principal = Depends(require_principal),
    _origin: None = Depends(_check_origin),
):
    """Tear down a streamable-http session (MCP 2025-03-26).

    400 when the Mcp-Session-Id header is missing, 404 for unknown sessions.
    """
    session_id = request.headers.get("mcp-session-id")
    if not session_id:
        raise HTTPException(status_code=400, detail="Mcp-Session-Id header required")
    if session_id not in _MCP_STREAMABLE_SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    _MCP_STREAMABLE_SESSIONS.pop(session_id, None)
    logger.info("MCP streamable-http session closed: %s", session_id)
    return {"ok": True}


@app.get("/sse")
async def sse_endpoint(
    request: Request,
    _principal: Principal = Depends(require_principal),
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
    _principal: Principal = Depends(require_principal),
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
# REST API — search and stats for the desktop app
# ---------------------------------------------------------------------------

class _SearchRequest(BaseModel):
    q: str
    limit: int = 20


# S-225: singleton CacheManager; initialised lazily on first search request when
# cache_enabled=True.  None means either disabled or not yet initialised.
_SEARCH_CACHE: Optional[Any] = None


def _get_search_cache() -> Any:
    """Return the CacheManager singleton, creating it on first call."""
    global _SEARCH_CACHE
    if _SEARCH_CACHE is None:
        from pathlib import Path

        from depthfusion.cache.manager import CacheManager

        cache_key_b64 = os.environ.get("DEPTHFUSION_CACHE_KEY")
        key_bytes = cache_key_b64.encode() if cache_key_b64 else None
        db_path = Path(os.environ.get(
            "DEPTHFUSION_CACHE_DB",
            str(Path.home() / ".claude" / ".depthfusion_search_cache.db"),
        ))
        _SEARCH_CACHE = CacheManager(db_path=db_path, key=key_bytes)
    return _SEARCH_CACHE


def _search_cache_key(q: str, limit: int, principal_id: str = "global") -> str:
    import hashlib
    return "search/" + hashlib.sha256(
        f"{principal_id}\x00{q}\x00{limit}".encode()
    ).hexdigest()[:32]


@app.post("/api/v1/search")
async def rest_search(
    body: _SearchRequest,
    _principal: Principal = Depends(require_principal),
):
    import hashlib

    from depthfusion.mcp.tools._shared import _tool_recall_impl  # lazy import

    config = DepthFusionConfig.from_env()

    # S-225: cache keyed by (principal, query, limit) to prevent cross-user cache hits
    if config.cache_enabled:
        cache = _get_search_cache()
        # principal_id is the JWT sub claim; hash it to a fixed-width key component
        principal_key = hashlib.sha256(_principal.principal_id.encode()).hexdigest()[:16]
        cache_key = _search_cache_key(body.q, body.limit, principal_key)
        entry = cache.get(cache_key, "global")
        if entry is not None and entry.data is not None:
            return json.loads(entry.data)

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None, _tool_recall_impl, {"query": body.q, "limit": body.limit}
    )
    data = json.loads(raw)
    results = [
        {
            "id": b.get("chunk_id", ""),
            "title": b.get("chunk_id", b.get("source", ""))
                .replace("-", " ").replace("_", " ").title(),
            "snippet": b.get("snippet", ""),
            "score": b.get("score", 0.0),
            "source": b.get("source", ""),
        }
        for b in data.get("blocks", [])
    ]
    response_body = {"results": results}
    if config.cache_enabled:
        cache = _get_search_cache()
        principal_key = hashlib.sha256(_principal.principal_id.encode()).hexdigest()[:16]
        cache_key = _search_cache_key(body.q, body.limit, principal_key)
        cache.put(cache_key, "global", json.dumps(response_body).encode())
    return response_body


@app.get("/api/v1/stats")
async def rest_stats(
    _: Principal = Depends(require_principal),
):
    import pathlib
    import sqlite3

    from depthfusion.core.project_registry import ProjectRegistry  # lazy import

    # HNSW index entry count (from the sidecar meta file — cheap, no model load)
    hnsw_meta_path = pathlib.Path(
        os.getenv("DEPTHFUSION_HNSW_INDEX_PATH", "~/.agent-mc/depthfusion/hnsw.bin")
    ).expanduser().with_suffix(".bin.meta.json")
    hnsw_count = 0
    if hnsw_meta_path.exists():
        try:
            hnsw_count = json.loads(hnsw_meta_path.read_text()).get("entry_count", 0)
        except Exception:  # noqa: BLE001
            pass

    # Memory store entry count
    config = DepthFusionConfig.from_env()
    mem_count = 0
    try:
        conn = sqlite3.connect(str(config.memory_store_path))
        mem_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
    except Exception:  # noqa: BLE001
        pass

    registry = ProjectRegistry()
    projects_list = registry.list_projects()
    last_synced = max(
        (p.last_synced for p in projects_list if p.last_synced),
        default=None,
    )

    return {
        "context_files": hnsw_count + mem_count,
        "projects": [p.slug for p in projects_list],
        "project_count": len(projects_list),
        "last_synced": last_synced,
    }


# ---------------------------------------------------------------------------
# Cognitive REST endpoints (S-232)
# ---------------------------------------------------------------------------

@app.get("/api/v1/cognitive/status")
async def rest_cognitive_status(
    _: Principal = Depends(require_principal),
):
    """Return persona, offload, and distillation status from the cognitive layer."""
    from depthfusion.mcp.tools.system import _tool_status  # lazy import

    config = DepthFusionConfig.from_env()
    raw = _tool_status(config)
    return json.loads(raw)


@app.post("/api/v1/cognitive/bridge")
async def rest_cognitive_bridge(
    request: Request,
    _: Principal = Depends(require_principal),
):
    """Retrieve raw offloaded text for a node_id from refs/."""
    from depthfusion.mcp.tools.bridge import _tool_bridge  # lazy import

    body: dict = await request.json()
    node_id = body.get("node_id", "")
    session_id = body.get("session_id", "")
    if not node_id:
        return {"error": "node_id is required"}
    config = DepthFusionConfig.from_env()
    raw = _tool_bridge({"node_id": node_id, "session_id": session_id}, config)
    return json.loads(raw)


@app.get("/api/v1/cognitive/scenarios")
async def rest_cognitive_scenarios(
    _: Principal = Depends(require_principal),
):
    """Return parsed scenario blocks for the current project."""
    import pathlib
    import re

    discoveries_dir = pathlib.Path("~/.claude/shared/discoveries").expanduser()
    scenarios: list[dict] = []
    project_ids: list[str] = []

    pattern = re.compile(r"^scenarios-(.+)\.md$")
    for path in sorted(discoveries_dir.glob("scenarios-*.md")):
        m = pattern.match(path.name)
        if not m:
            continue
        project_id = m.group(1)
        project_ids.append(project_id)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Parse ## Scene headings as scenario blocks
        blocks = re.split(r"\n(?=## )", text.strip())
        for block in blocks:
            lines = block.strip().splitlines()
            if not lines:
                continue
            title = lines[0].lstrip("# ").strip()
            body = "\n".join(lines[1:]).strip()
            scenarios.append({
                "project_id": project_id,
                "title": title,
                "summary": body[:400],
            })

    return {"scenarios": scenarios, "project_ids": project_ids}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the MCP HTTP/SSE server.

    Reads all configuration from environment variables.  The server is
    fail-closed: all requests to /sse and /messages require a valid Bearer
    token regardless of bind address.
    """
    host = get_mcp_bind_host()
    port = int(os.getenv("DEPTHFUSION_MCP_PORT", "7301"))

    logger.info(
        "DepthFusion MCP HTTP/SSE server starting on %s:%d", host, port
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
