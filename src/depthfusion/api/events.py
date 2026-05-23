"""Event Graph Fabric — REST router for /v1/events/* endpoints.

Implements:
  POST /v1/events/publish  — record an agent publish event in the graph + stream
  GET  /v1/events/stream   — SSE fan-out of live EventEntity objects

Authentication: Bearer token required whenever DEPTHFUSION_API_TOKEN is set.
Redis stays loopback-only; the Tailscale interface is handled by rest.py.

S-142 / T-484
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _check_fabric_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """Enforce Bearer token whenever DEPTHFUSION_API_TOKEN is configured.

    Stricter than _check_auth in rest.py: does not require DEPTHFUSION_API_PUBLIC=1.
    Any non-loopback bind (e.g. Tailscale) must carry a token.
    """
    token = os.getenv("DEPTHFUSION_API_TOKEN", "")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# EventStore singleton
# ---------------------------------------------------------------------------

_event_store = None
_event_store_lock = asyncio.Lock()


def _get_event_store_sync():
    """Synchronous lazy init used outside an async context (e.g. tests)."""
    global _event_store
    if _event_store is None:
        from depthfusion.core.event_store import EventStore, RedisStreamBackend
        from depthfusion.graph.store import get_store

        graph = get_store()
        redis_url = os.getenv("DEPTHFUSION_REDIS_URL", "")
        stream = RedisStreamBackend(redis_url) if redis_url else None
        _event_store = EventStore(graph=graph, stream=stream)
    return _event_store


async def _get_event_store():
    """Async-safe lazy singleton — serialises first-time construction."""
    global _event_store
    if _event_store is not None:
        return _event_store
    async with _event_store_lock:
        if _event_store is None:
            _event_store = await asyncio.get_event_loop().run_in_executor(
                None, _get_event_store_sync
            )
    return _event_store


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class PublishEventBody(BaseModel):
    agent_id: str
    project_slug: str
    memory_refs: list[str]
    session_id: Optional[str] = None
    event_type: str = "publish"


# ---------------------------------------------------------------------------
# POST /v1/events/publish
# ---------------------------------------------------------------------------

@router.post("/v1/events/publish")
async def publish_event(
    body: PublishEventBody,
    _auth: None = Depends(_check_fabric_auth),
):
    """Record an agent publish event in the graph and notify via stream.

    Returns ``{"event_id": str, "indexed": bool}``.
    Always returns ``indexed: true`` — the graph write is synchronous and
    durable before this response is sent.
    """
    store = await _get_event_store()
    event_id = await store.publish(
        agent_id=body.agent_id,
        project_slug=body.project_slug,
        memory_refs=body.memory_refs,
        event_type=body.event_type,
        session_id=body.session_id,
    )
    return {"event_id": event_id, "indexed": True}


# ---------------------------------------------------------------------------
# GET /v1/events/stream  (Server-Sent Events)
# ---------------------------------------------------------------------------

@router.get("/v1/events/stream")
async def stream_events(
    projects: str = Query(..., description="Comma-separated project slugs"),
    since_id: str = Query(default="$", description="Redis Stream entry ID for replay"),
    consumer_id: Optional[str] = Query(default=None),
    _auth: None = Depends(_check_fabric_auth),
):
    """SSE stream of EventEntity objects from the live event graph fabric.

    Yields ``data: <JSON>\\n\\n`` frames. Requires a StreamBackend (Redis).
    Returns a one-shot error frame and closes if no StreamBackend is configured.

    Client reconnection: pass the last ``entry_id`` received as ``since_id``
    to replay missed events.
    """
    project_list = [p.strip() for p in projects.split(",") if p.strip()]
    if not project_list:
        raise HTTPException(status_code=422, detail="projects must not be empty")

    store = await _get_event_store()

    async def _generate():
        try:
            async for entry_id, entity in store.subscribe_stream(
                projects=project_list,
                since_id=since_id,
                consumer_id=consumer_id,
            ):
                payload = {
                    "entry_id": entry_id,
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "type": entity.type,
                    "project": entity.project,
                    "first_seen": entity.first_seen,
                    "metadata": entity.metadata,
                }
                yield f"data: {json.dumps(payload)}\n\n"
        except RuntimeError as exc:
            # subscribe_stream raises RuntimeError when no StreamBackend configured
            log.warning("events/stream: %s", exc)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        except Exception as exc:
            log.error("events/stream: unexpected error — %s", exc, exc_info=True)
            yield f"data: {json.dumps({'error': 'internal stream error'})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
