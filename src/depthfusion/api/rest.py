"""DepthFusion REST API — FastAPI app, loopback by default.

Security: binds 127.0.0.1:7300 unless DEPTHFUSION_API_PUBLIC=1 AND
DEPTHFUSION_API_TOKEN is set. Startup raises ValueError if public
bind is requested without a bearer token.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException

app = FastAPI(title="DepthFusion Cognitive API", version="1.0.0")

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
