"""DepthFusion REST API — FastAPI app, loopback by default.

Security: binds 127.0.0.1:7300 unless DEPTHFUSION_API_PUBLIC=1 AND
DEPTHFUSION_API_TOKEN is set. Startup raises ValueError if public
bind is requested without a bearer token.

Query endpoints (/query/*) additionally support X-DepthFusion-Key header
auth controlled by DEPTHFUSION_QUERY_API_KEY env var.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query

app = FastAPI(
    title="DepthFusion Cognitive API",
    version="1.0.0",
    openapi_url="/openapi.json",
)

_API_TOKEN = os.getenv("DEPTHFUSION_API_TOKEN", "")
_API_PUBLIC = os.getenv("DEPTHFUSION_API_PUBLIC", "0") == "1"


def get_bind_host() -> str:
    if os.getenv("DEPTHFUSION_API_PUBLIC", "0") == "1":
        return "0.0.0.0"
    return "127.0.0.1"


def validate_public_bind_config() -> None:
    if os.getenv("DEPTHFUSION_API_PUBLIC", "0") == "1" and not os.getenv(
        "DEPTHFUSION_API_TOKEN", ""
    ):
        raise ValueError(
            "DEPTHFUSION_API_TOKEN must be set when DEPTHFUSION_API_PUBLIC=1. "
            "Public bind without bearer token authentication is forbidden."
        )


def _check_auth(authorization: Optional[str] = Header(default=None)) -> None:
    token = os.getenv("DEPTHFUSION_API_TOKEN", "")
    if os.getenv("DEPTHFUSION_API_PUBLIC", "0") == "1" and token:
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Unauthorized")


def _check_query_auth(
    x_depthfusion_key: Optional[str] = Header(default=None, alias="X-DepthFusion-Key"),
) -> None:
    """API key auth for /query/* endpoints. Only enforced when key is configured."""
    key = os.getenv("DEPTHFUSION_QUERY_API_KEY", "")
    if key and x_depthfusion_key != key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-DepthFusion-Key")


def _parse_dt(value: Optional[str], param_name: str) -> Optional[datetime]:
    """Parse ISO-8601 datetime string; raise 422 on invalid input."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid ISO-8601 datetime for '{param_name}': {value!r}",
        )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/v1/cognitive-state")
async def cognitive_state(
    project_id: str,
    _auth: None = Depends(_check_auth),
):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    cfg = DepthFusionConfig()
    store = MemoryStore(cfg.memory_store_path)
    log = EventLog(cfg.event_log_path)
    total = store.count(project_id)
    active = len(store.query(project_id=project_id, limit=1000))
    return {
        "project_id": project_id,
        "total_memories": total,
        "active_memories": active,
        "total_events": log.count(),
        "feature_flags": {
            "cognitive_retrieval": cfg.cognitive_retrieval,
            "contradiction_engine": cfg.contradiction_engine,
            "decision_memory": cfg.decision_memory,
            "operational_memory": cfg.operational_memory,
            "autonomic": cfg.autonomic,
        },
    }


@app.get("/v1/memories")
async def list_memories(
    project_id: str,
    memory_type: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 50,
    _auth: None = Depends(_check_auth),
):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.storage.memory_store import MemoryStore

    cfg = DepthFusionConfig()
    store = MemoryStore(cfg.memory_store_path)
    memories = store.query(
        project_id=project_id,
        include_archived=include_archived,
        memory_type=memory_type,
        limit=limit,
    )
    return {"memories": [m.to_dict() for m in memories], "count": len(memories)}


# ---------------------------------------------------------------------------
# Query endpoints — /query/discoveries, /query/sessions, /query/aggregate
# ---------------------------------------------------------------------------

@app.get("/query/discoveries")
async def get_discoveries(
    project: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated tags"),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    _auth: None = Depends(_check_query_auth),
):
    from depthfusion.api.query import query_discoveries

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    return query_discoveries(
        project=project,
        agent=agent,
        from_dt=from_dt,
        to_dt=to_dt,
        tags=tag_list,
        cursor=cursor,
        limit=limit,
    )


@app.get("/query/sessions")
async def get_sessions(
    project: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    _auth: None = Depends(_check_query_auth),
):
    from depthfusion.api.query import query_sessions

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")

    return query_sessions(
        project=project,
        agent=agent,
        from_dt=from_dt,
        to_dt=to_dt,
        cursor=cursor,
        limit=limit,
    )


@app.get("/query/aggregate")
async def get_aggregate(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    _auth: None = Depends(_check_query_auth),
):
    from depthfusion.api.query import query_aggregate

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")

    return query_aggregate(from_dt=from_dt, to_dt=to_dt)
