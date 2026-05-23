"""Event Graph Fabric — REST router for /v1/events/* and /v1/graph/* endpoints.

Implements:
  POST /v1/events/publish              — record an agent publish event
  GET  /v1/events/stream               — SSE fan-out of live EventEntity objects
  GET  /v1/events/seed                 — ranked context bundle for fabric_seed mode (S-143)
  GET  /v1/graph/agent/{agent_id}/trail    — provenance trail for one agent (S-144)
  GET  /v1/graph/memory/{entity_id}/observers — agents that received a memory (S-144)

Authentication: Bearer token required whenever DEPTHFUSION_API_TOKEN is set.
Redis stays loopback-only; the Tailscale interface is handled by rest.py.

S-142 / T-484; S-143 / T-490; S-144 / T-494 T-495
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# GET /v1/events/seed  (S-143 AC-3)
# ---------------------------------------------------------------------------

@router.get("/v1/events/seed")
async def events_seed(
    projects: str = Query(..., description="Comma-separated project slugs"),
    session_id: Optional[str] = Query(default=None),
    goal: str = Query(default="", description="Goal query for recall_relevance ranking"),
    top_k: int = Query(default=5, ge=1, le=20),
    since_hours: float = Query(default=24.0, gt=0.0, le=720.0),
    _auth: None = Depends(_check_fabric_auth),
):
    """Return a ranked context bundle for fabric_seed session warm-up.

    Ranking: ``recall_relevance × recency_decay × log(1 + observer_count)``

    ``observer_count`` is the number of distinct agent_ids that have an
    ``AGENT_RECEIVED`` edge to the memory entity.  ``recency_decay`` is
    ``exp(-days_since_first_seen / 7)``.

    Returns ``{"bundle": [...], "degraded": bool}`` where ``degraded: true``
    means Redis is unavailable and the bundle is derived from graph-only data.
    """
    project_list = [p.strip() for p in projects.split(",") if p.strip()]
    if not project_list:
        raise HTTPException(status_code=422, detail="projects must not be empty")

    store = await _get_event_store()
    result = await store.fabric_seed_bundle(
        projects=project_list,
        goal=goal,
        top_k=top_k,
        since_hours=since_hours,
    )
    if session_id:
        result["session_id"] = session_id
    return result


# ---------------------------------------------------------------------------
# GET /v1/graph/agent/{agent_id}/trail  (S-144 AC-1)
# ---------------------------------------------------------------------------

@router.get("/v1/graph/agent/{agent_id}/trail")
async def agent_trail(
    agent_id: str,
    project: Optional[str] = Query(default=None, description="Filter by project slug"),
    since: Optional[str] = Query(default=None, description="ISO-8601 lower bound (inclusive)"),
    until: Optional[str] = Query(default=None, description="ISO-8601 upper bound (inclusive)"),
    _auth: None = Depends(_check_fabric_auth),
):
    """Return AGENT_PUBLISHED + AGENT_RECEIVED EventEntities for ``agent_id``.

    Results are sorted by ``first_seen`` ascending.
    Returns ``{"trail": [...], "count": int}`` — empty list (not 404) when no
    events match the time range.
    """
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None
    try:
        if since:
            since_ts = datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp()
        if until:
            until_ts = datetime.fromisoformat(until).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid date format: {exc}")

    store = await _get_event_store()
    all_entities = await asyncio.get_event_loop().run_in_executor(
        None, store.graph.all_entities
    )

    trail = []
    for entity in all_entities:
        if entity.type != "event":
            continue
        meta = entity.metadata or {}
        if meta.get("agent_id") != agent_id:
            continue
        if project and meta.get("project_slug") != project:
            continue
        try:
            ts = datetime.fromisoformat(entity.first_seen).replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, TypeError):
            ts = 0.0
        if since_ts is not None and ts < since_ts:
            continue
        if until_ts is not None and ts > until_ts:
            continue
        trail.append({
            "entity_id": entity.entity_id,
            "event_type": meta.get("event_type", ""),
            "memory_refs": meta.get("memory_refs", []),
            "first_seen": entity.first_seen,
            "project": meta.get("project_slug", entity.project),
            "session_id": meta.get("session_id"),
        })

    trail.sort(key=lambda x: x["first_seen"])
    return {"trail": trail, "count": len(trail)}


# ---------------------------------------------------------------------------
# GET /v1/graph/memory/{entity_id}/observers  (S-144 AC-2)
# ---------------------------------------------------------------------------

@router.get("/v1/graph/memory/{entity_id}/observers")
async def memory_observers(
    entity_id: str,
    _auth: None = Depends(_check_fabric_auth),
):
    """Return all distinct ``agent_id`` values that have an AGENT_RECEIVED edge to ``entity_id``.

    Returns 404 if ``entity_id`` is not found in the graph.
    Returns ``{"observers": [...], "count": int}`` otherwise.
    Each observer entry: ``{"agent_id": str, "timestamp": str, "edge_id": str}``.
    """
    store = await _get_event_store()

    entity = await asyncio.get_event_loop().run_in_executor(
        None, store.graph.get_entity, entity_id
    )
    if entity is None:
        raise HTTPException(status_code=404, detail=f"entity {entity_id!r} not found")

    edges = await asyncio.get_event_loop().run_in_executor(
        None, lambda: store.graph.get_edges(entity_id, relationship_filter=["AGENT_RECEIVED"])
    )

    seen_agents: set[str] = set()
    observers = []
    for edge in edges:
        agent = edge.metadata.get("agent_id") or edge.target_id
        if agent in seen_agents:
            continue
        seen_agents.add(agent)
        observers.append({
            "agent_id": agent,
            "timestamp": edge.metadata.get("timestamp", ""),
            "edge_id": edge.edge_id,
        })

    observers.sort(key=lambda x: x["timestamp"])
    return {"observers": observers, "count": len(observers)}
