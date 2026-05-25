"""DepthFusion REST API — FastAPI app, loopback by default.

Security: binds 127.0.0.1:7300 unless DEPTHFUSION_API_PUBLIC=1 AND
DEPTHFUSION_API_TOKEN is set. Startup raises ValueError if public
bind is requested without a bearer token.

Tailscale multi-bind: set DEPTHFUSION_API_TAILSCALE=1 to spawn a second
uvicorn listener on the Tailscale interface IP (resolved via ``tailscale ip
-4`` at startup). Requires DEPTHFUSION_API_TOKEN. Fails gracefully (log
warning, loopback-only) if the tailscale command is unavailable or errors.
Redis stays loopback-only — never exposed on the Tailscale interface.

Query endpoints (/query/*) additionally support X-DepthFusion-Key header
auth controlled by DEPTHFUSION_QUERY_API_KEY env var.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from pydantic import BaseModel

from depthfusion.api.events import router as events_router

log = logging.getLogger(__name__)

app = FastAPI(
    title="DepthFusion Cognitive API",
    version="1.0.0",
    openapi_url="/openapi.json",
)

# Mount the Event Graph Fabric router (S-142 / T-486)
app.include_router(events_router)

_API_TOKEN = os.getenv("DEPTHFUSION_API_TOKEN", "")
_API_PUBLIC = os.getenv("DEPTHFUSION_API_PUBLIC", "0") == "1"


def get_bind_host() -> str:
    if os.getenv("DEPTHFUSION_API_PUBLIC", "0") == "1":
        return "0.0.0.0"
    return "127.0.0.1"


def validate_public_bind_config() -> None:
    if os.getenv("DEPTHFUSION_API_PUBLIC", "0") == "1":
        if not os.getenv("DEPTHFUSION_API_TOKEN", ""):
            raise ValueError(
                "DEPTHFUSION_API_TOKEN must be set when DEPTHFUSION_API_PUBLIC=1. "
                "Public bind without bearer token authentication is forbidden."
            )
        if not os.getenv("DEPTHFUSION_QUERY_API_KEY", ""):
            raise ValueError(
                "DEPTHFUSION_QUERY_API_KEY must be set when DEPTHFUSION_API_PUBLIC=1. "
                "Public bind exposes /query/* endpoints which require an API key."
            )
    if os.getenv("DEPTHFUSION_API_TAILSCALE", "0") == "1":
        if not os.getenv("DEPTHFUSION_API_TOKEN", ""):
            raise ValueError(
                "DEPTHFUSION_API_TOKEN must be set when DEPTHFUSION_API_TAILSCALE=1. "
                "Tailscale bind without bearer token authentication is forbidden."
            )


# ---------------------------------------------------------------------------
# Tailscale multi-bind (T-485)
# ---------------------------------------------------------------------------

def get_tailscale_ip() -> Optional[str]:
    """Return the Tailscale IPv4 address or None if unavailable."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            ip = result.stdout.strip()
            if ip:
                return ip
        log.warning(
            "rest: tailscale ip -4 exited %d: %s",
            result.returncode,
            result.stderr.strip(),
        )
    except FileNotFoundError:
        log.warning("rest: tailscale command not found — Tailscale bind skipped")
    except subprocess.TimeoutExpired:
        log.warning("rest: tailscale ip -4 timed out — Tailscale bind skipped")
    except Exception as exc:
        log.warning("rest: tailscale IP resolution failed — %s", exc)
    return None


def _run_tailscale_server(host: str, port: int) -> None:
    """Run a second uvicorn server on the Tailscale interface in a daemon thread."""
    try:
        import uvicorn  # type: ignore[import-untyped]
    except ImportError:
        log.warning("rest: uvicorn not available — Tailscale listener not started")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    config = uvicorn.Config(app=app, host=host, port=port, loop="none", log_level="info")
    server = uvicorn.Server(config)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()


def start_tailscale_listener() -> None:
    """Spawn a second uvicorn listener on the Tailscale interface if configured.

    Fails gracefully: logs a warning and returns without raising if Tailscale
    is unavailable or the IP cannot be resolved. Redis is never exposed on
    this interface — only the HTTP API is served here.
    """
    if os.getenv("DEPTHFUSION_API_TAILSCALE", "0") != "1":
        return

    ts_ip = get_tailscale_ip()
    if not ts_ip:
        log.warning(
            "rest: DEPTHFUSION_API_TAILSCALE=1 but no Tailscale IP found — "
            "serving loopback only"
        )
        return

    port = int(os.getenv("DEPTHFUSION_API_PORT", "7300"))
    log.info("rest: starting Tailscale listener on %s:%d", ts_ip, port)

    t = threading.Thread(
        target=_run_tailscale_server,
        args=(ts_ip, port),
        daemon=True,
        name="depthfusion-tailscale-listener",
    )
    t.start()


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

    cfg = DepthFusionConfig.from_env()
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

    cfg = DepthFusionConfig.from_env()
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

    try:
        return query_discoveries(
            project=project,
            agent=agent,
            from_dt=from_dt,
            to_dt=to_dt,
            tags=tag_list,
            cursor=cursor,
            limit=limit,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid cursor")


@app.get("/query/sessions")
async def get_sessions(
    project: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    include_telemetry_summary: bool = Query(default=False),
    _auth: None = Depends(_check_query_auth),
):
    from depthfusion.api.query import query_sessions

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")

    try:
        result = query_sessions(
            project=project,
            agent=agent,
            from_dt=from_dt,
            to_dt=to_dt,
            cursor=cursor,
            limit=limit,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid cursor")

    if include_telemetry_summary:
        try:
            from depthfusion.core.config import DepthFusionConfig
            from depthfusion.storage.telemetry_store import TelemetryStore

            cfg = DepthFusionConfig.from_env()
            store = TelemetryStore(cfg.telemetry_store_path)
            tel = store.aggregate(
                project=project,
                agent=agent,
                from_dt=from_dt.isoformat() if from_dt else None,
                to_dt=to_dt.isoformat() if to_dt else None,
            )
            result["telemetry_summary"] = tel["rows"][0] if tel["rows"] else None
        except Exception:
            result["telemetry_summary"] = None

    return result


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


def _decode_telemetry_cursor(cursor: Optional[str]) -> int:
    """Decode cursor → integer offset; raises HTTPException 422 on invalid non-empty cursor."""
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid cursor")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


# ---------------------------------------------------------------------------
# Telemetry query endpoints — /query/telemetry, /query/telemetry/aggregate
# ---------------------------------------------------------------------------

@app.get("/query/telemetry")
async def get_telemetry(
    project: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    session_type: Optional[str] = Query(default=None),
    story_id: Optional[str] = Query(default=None),
    sprint: Optional[str] = Query(default=None),
    tool_name: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    include_think_time: bool = Query(default=False),
    _auth: None = Depends(_check_query_auth),
):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.storage.telemetry_store import TelemetryStore, compute_think_times

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")
    offset = _decode_telemetry_cursor(cursor)

    cfg = DepthFusionConfig.from_env()
    store = TelemetryStore(cfg.telemetry_store_path)
    rows = store.query(
        project=project,
        agent=agent,
        session_type=session_type,
        story_id=story_id,
        sprint=sprint,
        tool_name=tool_name,
        from_dt=from_dt.isoformat() if from_dt else None,
        to_dt=to_dt.isoformat() if to_dt else None,
        limit=limit + 1,
        offset=offset,
    )

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    if include_think_time:
        rows = compute_think_times(rows)

    next_cursor = _encode_cursor(offset + limit) if has_more else None
    return {"rows": rows, "row_count": len(rows), "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# Request body models for mutation endpoints
# ---------------------------------------------------------------------------

class ScopeBody(BaseModel):
    scope: str

class SessionSeedBody(BaseModel):
    project: str
    branch: Optional[str] = None
    context: Optional[str] = None

class CompressSessionBody(BaseModel):
    max_tokens: Optional[int] = None

class TagSessionBody(BaseModel):
    tags: list[str]

class RecallBody(BaseModel):
    query: str
    limit: Optional[int] = 5
    threshold: Optional[float] = 0.7
    scope: Optional[str] = None

class RecallFeedbackBody(BaseModel):
    recall_id: str
    rating: int
    notes: Optional[str] = None

class PublishContextBody(BaseModel):
    content: str
    tags: list[str]
    project: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Optional[dict] = None

class RetrieveContextBody(BaseModel):
    id: Optional[str] = None
    tags: Optional[list[str]] = None
    project: Optional[str] = None

class AutoLearnBody(BaseModel):
    session_id: Optional[str] = None
    depth: Optional[str] = None

class GraphTraverseBody(BaseModel):
    from_node: str
    depth: Optional[int] = 2
    direction: Optional[str] = "both"
    filter_tags: Optional[list[str]] = None

class RunRecursiveBody(BaseModel):
    query: str
    max_depth: Optional[int] = 3

class SupersedeBody(BaseModel):
    project_id: str
    old_memory_id: str
    new_memory_id: str
    reason: Optional[str] = None
    actor: Optional[str] = None

class PruneDiscoveriesBody(BaseModel):
    older_than_days: Optional[int] = 30
    status: Optional[str] = None

class SetMemoryScoreBody(BaseModel):
    score: float

class RecordTelemetryBody(BaseModel):
    event: str
    data: dict
    session_id: Optional[str] = None

class RecordDecisionBody(BaseModel):
    decision: str
    rationale: str
    context: Optional[str] = None
    project: Optional[str] = None

class RecordIncidentBody(BaseModel):
    description: str
    severity: str
    impact: Optional[str] = None

class ReportOutcomeBody(BaseModel):
    task_id: str
    outcome: str
    notes: Optional[str] = None

class SkillCandidatesBody(BaseModel):
    query: str
    limit: Optional[int] = 5
    project: Optional[str] = None

class ConfirmDiscoveryBody(BaseModel):
    text: str
    project: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[float] = None


class PinDiscoveryBody(BaseModel):
    filename: str
    pinned: bool = True


class InspectDiscoveryBody(BaseModel):
    filename: str


def _parse_tool_result(result):
    """Parse a _tool_* return value: JSON string → dict, or wrap plain string."""
    import json
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return {"result": result}
    return result


# ---------------------------------------------------------------------------
# Status and capability endpoints
# ---------------------------------------------------------------------------

@app.get("/status")
async def status(_auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_status
    cfg = DepthFusionConfig.from_env()
    return _parse_tool_result(_tool_status(cfg))


@app.get("/tiers/status")
async def tiers_status(_auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_tier_status
    return _parse_tool_result(_tool_tier_status())


@app.get("/capabilities")
async def capabilities(_auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_describe_capabilities
    return _parse_tool_result(_tool_describe_capabilities())


@app.get("/hnsw/capability")
async def hnsw_capability(_auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_hnsw_capability
    return _parse_tool_result(_tool_hnsw_capability())


@app.get("/cognitive-state")
async def cognitive_state_v2(_auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_get_cognitive_state
    cfg = DepthFusionConfig.from_env()
    return _parse_tool_result(_tool_get_cognitive_state({}, cfg))


# ---------------------------------------------------------------------------
# Session lifecycle endpoints
# ---------------------------------------------------------------------------

@app.put("/scope")
async def set_scope(body: ScopeBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_set_scope
    return _parse_tool_result(_tool_set_scope({"scope": body.scope}))


@app.post("/session/seed")
async def session_seed(body: SessionSeedBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_session_seed
    args = {"project": body.project}
    if body.branch is not None:
        args["branch"] = body.branch
    if body.context is not None:
        args["context"] = body.context
    return _parse_tool_result(_tool_session_seed(args))


@app.post("/session/compress")
async def session_compress(body: CompressSessionBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_compress_session
    args = {}
    if body.max_tokens is not None:
        args["max_tokens"] = body.max_tokens
    return _parse_tool_result(_tool_compress_session(args))


@app.post("/session/tags")
async def session_tags(body: TagSessionBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_tag_session
    return _parse_tool_result(_tool_tag_session({"tags": body.tags}))


# ---------------------------------------------------------------------------
# Recall endpoints
# ---------------------------------------------------------------------------

@app.post("/recall")
async def recall(body: RecallBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_recall
    args: dict = {"query": body.query, "limit": body.limit, "threshold": body.threshold}
    if body.scope is not None:
        args["scope"] = body.scope
    return _parse_tool_result(_tool_recall(args))


@app.post("/recall/feedback")
async def recall_feedback(body: RecallFeedbackBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_recall_feedback
    args: dict = {"recall_id": body.recall_id, "rating": body.rating}
    if body.notes is not None:
        args["notes"] = body.notes
    return _parse_tool_result(_tool_recall_feedback(args))


# ---------------------------------------------------------------------------
# Context endpoints
# ---------------------------------------------------------------------------

@app.post("/context")
async def publish_context(body: PublishContextBody, _auth: None = Depends(_check_auth)):
    import uuid
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_publish_context
    cfg = DepthFusionConfig.from_env()
    meta: dict = dict(body.metadata or {})
    if body.project is not None:
        meta["project"] = body.project
    if body.session_id is not None:
        meta["session_id"] = body.session_id
    item: dict = {
        "item_id": str(uuid.uuid4()),
        "content": body.content,
        "source_agent": "rest-api",
        "tags": body.tags,
    }
    if meta:
        item["metadata"] = meta
    return _parse_tool_result(_tool_publish_context({"item": item}, cfg))


@app.post("/context/retrieve")
async def retrieve_context(body: RetrieveContextBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_retrieve_context
    cfg = DepthFusionConfig.from_env()
    args: dict = {}
    if body.id is not None:
        args["id"] = body.id
    if body.tags is not None:
        args["tags"] = body.tags
    if body.project is not None:
        args["project"] = body.project
    return _parse_tool_result(_tool_retrieve_context(args, cfg))


@app.post("/auto-learn")
async def auto_learn(body: AutoLearnBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_auto_learn
    args: dict = {}
    if body.session_id is not None:
        args["session_id"] = body.session_id
    if body.depth is not None:
        args["depth"] = body.depth
    return _parse_tool_result(_tool_auto_learn(args))


# ---------------------------------------------------------------------------
# Graph endpoints
# ---------------------------------------------------------------------------

@app.get("/graph/status")
async def graph_status(_auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_graph_status
    return _parse_tool_result(_tool_graph_status())


@app.post("/graph/traverse")
async def graph_traverse(body: GraphTraverseBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_graph_traverse
    args: dict = {"from": body.from_node, "depth": body.depth, "direction": body.direction}
    if body.filter_tags is not None:
        args["filter_tags"] = body.filter_tags
    return _parse_tool_result(_tool_graph_traverse(args))


@app.post("/run/recursive")
async def run_recursive(body: RunRecursiveBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_run_recursive
    cfg = DepthFusionConfig.from_env()
    args: dict = {"query": body.query, "max_depth": body.max_depth}
    return _parse_tool_result(_tool_run_recursive(args, cfg))


# ---------------------------------------------------------------------------
# Discovery endpoints
# discoveries use filesystem filenames as identifiers (URL-encoded in path)
# ---------------------------------------------------------------------------

@app.get("/discoveries")
async def list_discoveries(
    project: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated tags"),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    _auth: None = Depends(_check_auth),
):
    from depthfusion.api.query import query_discoveries

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    try:
        return query_discoveries(
            project=project,
            agent=agent,
            from_dt=from_dt,
            to_dt=to_dt,
            tags=tag_list,
            cursor=cursor,
            limit=limit,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid cursor")


@app.post("/discoveries/inspect")
async def inspect_discovery(body: InspectDiscoveryBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_inspect_discovery
    return _parse_tool_result(_tool_inspect_discovery({"filename": body.filename}))


@app.post("/discoveries/confirm")
async def confirm_discovery(body: ConfirmDiscoveryBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_confirm_discovery
    args: dict = {"text": body.text}
    if body.project is not None:
        args["project"] = body.project
    if body.category is not None:
        args["category"] = body.category
    if body.confidence is not None:
        args["confidence"] = body.confidence
    return _parse_tool_result(_tool_confirm_discovery(args))


@app.post("/discoveries/pin")
async def pin_discovery(body: PinDiscoveryBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_pin_discovery
    return _parse_tool_result(
        _tool_pin_discovery({"filename": body.filename, "pinned": body.pinned})
    )


@app.post("/discoveries/supersede")
async def mark_superseded(body: SupersedeBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_mark_superseded
    cfg = DepthFusionConfig.from_env()
    args: dict = {
        "project_id": body.project_id,
        "old_memory_id": body.old_memory_id,
        "new_memory_id": body.new_memory_id,
    }
    if body.reason is not None:
        args["reason"] = body.reason
    if body.actor is not None:
        args["actor"] = body.actor
    return _parse_tool_result(_tool_mark_superseded(args, cfg))


@app.post("/discoveries/prune")
async def prune_discoveries(body: PruneDiscoveriesBody, _auth: None = Depends(_check_auth)):
    from depthfusion.mcp.server import _tool_prune_discoveries
    args: dict = {"older_than_days": body.older_than_days}
    if body.status is not None:
        args["status"] = body.status
    return _parse_tool_result(_tool_prune_discoveries(args))


# ---------------------------------------------------------------------------
# Memory scoring
# ---------------------------------------------------------------------------

@app.put("/memories/{memory_id}/score")
async def set_memory_score(
    memory_id: str = Path(...),
    body: SetMemoryScoreBody = ...,  # type: ignore[assignment]
    _auth: None = Depends(_check_auth),
):
    from depthfusion.mcp.server import _tool_set_memory_score
    return _parse_tool_result(_tool_set_memory_score({"id": memory_id, "score": body.score}))


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

@app.post("/telemetry")
async def record_telemetry(body: RecordTelemetryBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_record_telemetry
    cfg = DepthFusionConfig.from_env()
    args: dict = {"event": body.event, "data": body.data}
    if body.session_id is not None:
        args["session_id"] = body.session_id
    return _parse_tool_result(_tool_record_telemetry(args, cfg))


# ---------------------------------------------------------------------------
# Decisions, incidents, outcomes
# ---------------------------------------------------------------------------

@app.post("/decisions")
async def record_decision(body: RecordDecisionBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_record_decision
    cfg = DepthFusionConfig.from_env()
    args: dict = {"decision": body.decision, "rationale": body.rationale}
    if body.context is not None:
        args["context"] = body.context
    if body.project is not None:
        args["project"] = body.project
    return _parse_tool_result(_tool_record_decision(args, cfg))


@app.post("/incidents")
async def record_incident(body: RecordIncidentBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_record_incident
    cfg = DepthFusionConfig.from_env()
    args: dict = {"description": body.description, "severity": body.severity}
    if body.impact is not None:
        args["impact"] = body.impact
    return _parse_tool_result(_tool_record_incident(args, cfg))


@app.post("/outcomes")
async def report_outcome(body: ReportOutcomeBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_report_outcome
    cfg = DepthFusionConfig.from_env()
    args: dict = {"task_id": body.task_id, "outcome": body.outcome}
    if body.notes is not None:
        args["notes"] = body.notes
    return _parse_tool_result(_tool_report_outcome(args, cfg))


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

@app.post("/skills/candidates")
async def surface_skill_candidates(body: SkillCandidatesBody, _auth: None = Depends(_check_auth)):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _tool_surface_skill_candidates
    cfg = DepthFusionConfig.from_env()
    args: dict = {"query": body.query, "limit": body.limit}
    if body.project is not None:
        args["project"] = body.project
    return _parse_tool_result(_tool_surface_skill_candidates(args, cfg))


@app.get("/query/telemetry/aggregate")
async def get_telemetry_aggregate(
    project: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    session_type: Optional[str] = Query(default=None),
    story_id: Optional[str] = Query(default=None),
    sprint: Optional[str] = Query(default=None),
    period: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    _auth: None = Depends(_check_query_auth),
):
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.storage.telemetry_store import TelemetryStore

    if period and period not in ("day", "week", "month"):
        raise HTTPException(status_code=422, detail="period must be 'day', 'week', or 'month'")

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")

    cfg = DepthFusionConfig.from_env()
    store = TelemetryStore(cfg.telemetry_store_path)
    return store.aggregate(
        project=project,
        agent=agent,
        session_type=session_type,
        story_id=story_id,
        sprint=sprint,
        period=period,
        from_dt=from_dt.isoformat() if from_dt else None,
        to_dt=to_dt.isoformat() if to_dt else None,
    )
