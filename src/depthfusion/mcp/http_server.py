"""DepthFusion MCP HTTP/SSE server — JSON-RPC 2.0 over Server-Sent Events.

Transport: two-endpoint SSE pattern (MCP spec 2025-03-26)
  GET  /sse             → long-lived SSE stream; sends endpoint event + 30s pings
  POST /messages        → receives JSON-RPC body, dispatches via _process_request,
                          pushes response onto the session SSE queue
  GET  /health          → unauthenticated health probe

Security:
  - Default bind: 127.0.0.1:7301 (loopback)
  - DEPTHFUSION_MCP_PUBLIC=1 → binds 0.0.0.0 (requires token auth)
  - JWT validation: JWKS-backed RS256 when DEPTHFUSION_JWKS_URI is set.
    Falls back to static bearer token (DEPTHFUSION_MCP_TOKEN) for loopback-only
    dev deployments without an OIDC provider.
  - startup raises ValueError if public bind is requested without token auth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from importlib.metadata import version as _pkg_version
from typing import AsyncGenerator, Optional

_VERSION = _pkg_version("depthfusion")

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from depthfusion.core.config import DepthFusionConfig
from depthfusion.identity.errors import IdentityError, JwksFetchError, TokenExpiredError
from depthfusion.identity.jwks_cache import JwksCache
from depthfusion.identity.token_validator import TokenValidator
from depthfusion.mcp.server import _process_request

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
    if os.getenv("DEPTHFUSION_MCP_PUBLIC", "0") == "1":
        return "0.0.0.0"  # noqa: S104 — guarded by validate_mcp_public_bind
    return "127.0.0.1"


def validate_mcp_public_bind() -> None:
    if os.getenv("DEPTHFUSION_MCP_PUBLIC", "0") == "1" and not os.getenv(
        "DEPTHFUSION_MCP_TOKEN", ""
    ) and not os.getenv("DEPTHFUSION_JWKS_URI", ""):
        raise ValueError(
            "Either DEPTHFUSION_MCP_TOKEN or DEPTHFUSION_JWKS_URI must be set "
            "when DEPTHFUSION_MCP_PUBLIC=1. "
            "Public bind without bearer token authentication is forbidden."
        )


# ---------------------------------------------------------------------------
# Auth dependency — always enforced on /sse and /messages
# ---------------------------------------------------------------------------

async def _check_mcp_auth(
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Validate Bearer token on every authenticated endpoint.

    Validation order:
      1. JWKS JWT validation when DEPTHFUSION_JWKS_URI/OIDC_ISSUER/OIDC_AUDIENCE
         are all set — full RS256 signature + claim checks via identity module.
      2. Static bearer token comparison (DEPTHFUSION_MCP_TOKEN) — dev/loopback
         fallback when no OIDC provider is configured.
      3. Loopback-only, no token configured → pass (127.0.0.1 bind is the guard).
    """
    if not authorization or not authorization.startswith("Bearer "):
        _raise_if_auth_required(authorization)
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

    # Static bearer token fallback
    static_token = os.getenv("DEPTHFUSION_MCP_TOKEN", "")
    if static_token and raw_token != static_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _raise_if_auth_required(authorization: Optional[str]) -> None:
    """Reject unauthenticated requests on public-bind servers."""
    if os.getenv("DEPTHFUSION_MCP_PUBLIC", "0") == "1":
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Loopback-only with no token configured: allow (bind address is the guard)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "transport": "sse", "version": _VERSION}


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
    startup if DEPTHFUSION_MCP_PUBLIC=1 without any auth configuration.
    """
    validate_mcp_public_bind()

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
