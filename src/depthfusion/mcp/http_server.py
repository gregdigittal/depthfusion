"""DepthFusion MCP HTTP/SSE server — JSON-RPC 2.0 over Server-Sent Events.

Transport: two-endpoint SSE pattern (MCP spec 2025-03-26)
  GET  /sse             → long-lived SSE stream; sends endpoint event + 30s pings
  POST /messages        → receives JSON-RPC body, dispatches via _process_request,
                          pushes response onto the session SSE queue
  GET  /health          → unauthenticated health probe

Security:
  - Bind host: controlled by DEPTHFUSION_MCP_HOST (default 127.0.0.1 / loopback).
    Set to 0.0.0.0 only when exposing the server to remote clients AND auth is
    fully configured (see below).
  - Fail-closed auth: every request to /sse and /messages MUST carry a valid
    Bearer token.  If no token is present, or no auth backend is configured,
    the server returns HTTP 401.  There is no pass-through mode.
  - JWT validation: JWKS-backed RS256 when DEPTHFUSION_JWKS_URI is set
    (together with DEPTHFUSION_OIDC_ISSUER and DEPTHFUSION_OIDC_AUDIENCE).
    Falls back to static bearer token comparison (DEPTHFUSION_MCP_TOKEN) when
    the OIDC vars are absent.
  - /health is the only unauthenticated endpoint.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from importlib.metadata import version as _pkg_version
from typing import Any, AsyncGenerator, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from depthfusion.api.auth import require_principal
from depthfusion.core.config import DepthFusionConfig
from depthfusion.identity.errors import IdentityError, JwksFetchError, TokenExpiredError
from depthfusion.identity.jwks_cache import JwksCache
from depthfusion.identity.models import Principal
from depthfusion.identity.token_validator import TokenValidator
from depthfusion.mcp.server import _process_request

_VERSION = _pkg_version("depthfusion")

logger = logging.getLogger(__name__)

app = FastAPI(title="DepthFusion MCP HTTP/SSE", version=_VERSION)

_MCP_SESSIONS: dict[str, asyncio.Queue] = {}

_PING_INTERVAL = 30.0

# ---------------------------------------------------------------------------
# JWKS-backed JWT validator — initialised lazily on first auth call.
# A single instance is shared across all requests; JwksCache is concurrency-safe.
# ---------------------------------------------------------------------------

_token_validator: Optional[TokenValidator] = None


def _get_token_validator() -> Optional[TokenValidator]:
    """Return a JWT validator if OIDC env vars are fully configured, else None."""
    global _token_validator
    if _token_validator is not None:
        return _token_validator

    jwks_uri = os.getenv("DEPTHFUSION_JWKS_URI", "")
    issuer = os.getenv("DEPTHFUSION_OIDC_ISSUER", "")
    audience = os.getenv("DEPTHFUSION_OIDC_AUDIENCE", "")

    if jwks_uri and issuer and audience:
        cache = JwksCache(jwks_uri=jwks_uri)
        _token_validator = TokenValidator(
            jwks_cache=cache,
            expected_issuer=issuer,
            expected_audience=audience,
        )
        logger.info("MCP auth: JWKS JWT validation active (issuer=%s)", issuer)
    else:
        logger.warning(
            "MCP auth: DEPTHFUSION_JWKS_URI/OIDC_ISSUER/OIDC_AUDIENCE not set; "
            "falling back to static bearer token."
        )

    return _token_validator


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
# Auth dependency — always enforced on /sse and /messages
# ---------------------------------------------------------------------------

async def _check_mcp_auth(
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Validate Bearer token on every authenticated endpoint — fail closed.

    The server ALWAYS requires a valid Bearer token.  If no token is present,
    or if no auth backend is configured, the request is rejected with HTTP 401.

    Validation order:
      1. No Authorization header (or header without "Bearer " prefix) → 401.
      2. JWKS JWT validation when DEPTHFUSION_JWKS_URI/OIDC_ISSUER/OIDC_AUDIENCE
         are all set — full RS256 signature + claim checks via identity module.
         Exception ordering: TokenExpiredError → 401; JwksFetchError → 503;
         IdentityError → 401.
      3. Static bearer token comparison (DEPTHFUSION_MCP_TOKEN) — dev fallback
         when no OIDC provider is configured.
      4. No auth backend configured at all → 401 (fail closed).
    """
    if not authorization or not authorization.startswith("Bearer "):
        _raise_if_auth_required()
        return

    raw_token = authorization[len("Bearer "):]

    validator = _get_token_validator()

    if validator is not None:
        # Full JWT validation path
        try:
            await validator.validate(raw_token)
        except TokenExpiredError as exc:
            raise HTTPException(status_code=401, detail=f"Token expired: {exc}") from exc
        except JwksFetchError as exc:
            raise HTTPException(status_code=503, detail=f"Auth service unavailable: {exc}") from exc
        except IdentityError as exc:
            raise HTTPException(status_code=401, detail=f"Unauthorized: {exc}") from exc
        return

    # Static bearer token fallback — timing-safe comparison (secrets.compare_digest
    # prevents leaking token prefix length via short-circuit string equality)
    static_token = os.getenv("DEPTHFUSION_MCP_TOKEN", "")
    if static_token:
        import secrets
        if not secrets.compare_digest(raw_token, static_token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return

    # No auth backend configured — always fail closed
    raise HTTPException(status_code=401, detail="Unauthorized")


def _raise_if_auth_required() -> None:
    """Reject any request that carries no Bearer token — always.

    The server is fail-closed: missing tokens are never permitted regardless
    of bind address.
    """
    raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "transport": "sse", "version": _VERSION}


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


def _search_cache_key(q: str, limit: int) -> str:
    import hashlib
    return "search/" + hashlib.sha256(f"{q}\x00{limit}".encode()).hexdigest()[:32]


@app.post("/api/v1/search")
async def rest_search(
    body: _SearchRequest,
    _: None = Depends(_check_mcp_auth),
):
    from depthfusion.mcp.tools._shared import _tool_recall_impl  # lazy import

    config = DepthFusionConfig.from_env()

    # S-225: check Fernet cache on hit; populate on miss.
    if config.cache_enabled:
        cache = _get_search_cache()
        cache_key = _search_cache_key(body.q, body.limit)
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
        cache_key = _search_cache_key(body.q, body.limit)
        cache.put(cache_key, "global", json.dumps(response_body).encode())
    return response_body


@app.get("/api/v1/stats")
async def rest_stats(
    _: None = Depends(_check_mcp_auth),
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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the MCP HTTP/SSE server.

    Reads all configuration from environment variables.  The server is
    fail-closed: all requests to /sse and /messages require a valid Bearer
    token regardless of bind address.
    """
    # Initialise the validator at startup so config errors surface immediately
    _get_token_validator()

    host = get_mcp_bind_host()
    port = int(os.getenv("DEPTHFUSION_MCP_PORT", "7301"))

    logger.info(
        "DepthFusion MCP HTTP/SSE server starting on %s:%d", host, port
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
