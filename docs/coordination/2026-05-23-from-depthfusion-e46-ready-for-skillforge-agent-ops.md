# DepthFusion → SkillForge / Agent-Ops — E-46 Event Graph Fabric

- **From:** DepthFusion project (Greg / Claude)
- **To:** SkillForge / agent-ops
- **Date:** 2026-05-23
- **Trigger:** E-46 Event Graph Fabric shipped in v1.2.0 (HEAD `475a9e2` on `main`)
- **Compatibility verdict:** **ADDITIVE — no breaking changes to existing surface**

---

## TL;DR

E-46 adds a multi-agent shared memory layer on top of the existing MCP surface. All 29 pre-E-46 tools and their response shapes are unchanged. Three new MCP tools are available. The REST API has five new endpoints. Redis is optional — the graph writes succeed with or without it.

Agent-ops and SkillForge can consume the new tools immediately if the consuming host runs DepthFusion v1.2.0+ (pin to `475a9e2` or any later SHA on `main`).

---

## What shipped in E-46

### New MCP tools (3)

| Tool | What it does | Requires Redis? |
|---|---|---|
| `depthfusion_event_publish` | Publish an agent memory event to the graph + stream. Creates an `event` Entity with `AGENT_PUBLISHED` edge. | No (graph write always; stream best-effort) |
| `depthfusion_event_seed` | Get a ranked context bundle for `fabric_seed` session warm-up. Returns memories ranked by `recall_relevance × recency_decay × log(1 + observer_count)`. | No (falls back to graph-only if Redis absent) |
| `depthfusion_agent_trail` | Return all `AGENT_PUBLISHED` + `AGENT_RECEIVED` events for a given agent_id, filtered by project + time range. Provenance query. | No |

### Extended existing tool (1)

`depthfusion_session_seed` now accepts `mode: "fabric_seed"` in addition to the existing `"recall"` default. The existing default behaviour is unchanged.

### New REST endpoints (5)

All require `Authorization: Bearer $DEPTHFUSION_API_TOKEN`.

| Endpoint | Description |
|---|---|
| `POST /v1/events/publish` | Publish an event (same as MCP tool, HTTP interface) |
| `GET /v1/events/stream` | SSE stream — subscribe to live agent events by project |
| `GET /v1/events/seed` | fabric_seed bundle (same as MCP tool, HTTP interface) |
| `GET /v1/graph/agent/{agent_id}/trail` | Agent provenance trail (same as MCP tool, HTTP) |
| `GET /v1/graph/memory/{entity_id}/observers` | All agents with `AGENT_RECEIVED` edge to a memory entity |

Full endpoint docs: `docs/fabric/api-reference.md`

### New graph schema elements

| Element | Type | Purpose |
|---|---|---|
| `event` | `Entity.type` | First-class event node (publish, receive, seed) |
| `AGENT_PUBLISHED` | `Edge.relationship` | Agent → EventEntity edge on publish |
| `AGENT_RECEIVED` | `Edge.relationship` | Agent → EventEntity edge on SSE receipt |
| `SAME_SESSION_AS` | `Edge.relationship` | EventEntities within one session linked |
| `DERIVED_FROM` | `Edge.relationship` | CEP-detected convergence (Kafka/Flink path) |

The `event` entity type is additive — no existing entity types changed.

---

## Breaking changes

**None.** All 29 pre-E-46 tools respond identically. The `depthfusion_session_seed` change is purely additive (new optional parameter with the old behaviour as default).

---

## What agent-ops / SkillForge may want to consume

### Scenario 1 — Multi-agent session warm-start

When agent-ops spawns a new worker agent for a project, call `depthfusion_event_seed` (or `depthfusion_session_seed` with `mode: "fabric_seed"`) before the worker's first user-visible action. The returned bundle contains the top-k memories from across all agents that have worked on that project, ranked by freshness and team consensus.

```json
// MCP call
{
  "tool": "depthfusion_event_seed",
  "params": {
    "projects": ["depthfusion"],
    "goal": "implement auth refactor",
    "session_id": "agent-worker-007"
  }
}
// Returns: { "bundle": [...], "degraded": false, "session_id": "agent-worker-007" }
```

### Scenario 2 — Publish discovery from worker to shared memory

When a worker agent surfaces a significant finding, publish it so other agents inherit it immediately:

```json
{
  "tool": "depthfusion_event_publish",
  "params": {
    "agent_id": "agent-worker-007",
    "project_slug": "depthfusion",
    "memory_refs": ["sha256-of-discovery-content"],
    "session_id": "agent-worker-007"
  }
}
```

### Scenario 3 — Provenance query in /history

The `/history` command in agent-ops can surface agent trail data:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:7300/v1/graph/agent/agent-worker-007/trail?project=depthfusion&since=2026-05-20T00:00:00Z"
```

---

## Dependency notes

### Redis (optional)

The live SSE stream (`/v1/events/stream`) requires Redis. Without Redis:
- `depthfusion_event_publish` still writes to the graph (graph writes are always synchronous)
- `depthfusion_event_seed` falls back to graph-only traversal (no live stream)
- Response includes `"degraded": true` flag
- No exceptions are raised — Redis failure is logged as a warning

Redis stays loopback-only per `infra-exposure.md`:
```
DEPTHFUSION_REDIS_URL=redis://127.0.0.1:6379/0
```

### Tailscale (optional)

For cross-machine multi-agent use, set `DEPTHFUSION_API_TAILSCALE=1` and `DEPTHFUSION_API_TOKEN`. See `docs/fabric/tailscale-setup.md`.

---

## Version pin

Recommended pin: `475a9e2` (HEAD on `main` as of 2026-05-23).

All E-46 work landed across commits:
- S-141/S-142 — EventStore, StreamBackend, REST endpoints
- S-143/S-144 — fabric_seed, MCP tools, provenance endpoints  
- S-145/S-146 — perf baselines, docs

The `pyproject.toml` version reads `1.2.0`. This is the first release where E-46 is shipped.

---

## SkillForge portability note

E-46's new modules (`core/event_store.py`, `api/events.py`) follow the same `StreamBackend` Protocol pattern as the existing `GraphBackend`. Per the existing `skillforge-integration-plan.md` portability table:

| Module | Strategy | Notes |
|---|---|---|
| `core/event_store.py` | SIDECAR (initially) | Depends on `redis.asyncio`; keep as Python, expose via REST |
| `api/events.py` | SIDECAR | FastAPI router — call from SkillForge via HTTP |
| `StreamBackend` Protocol | PORT (eventually) | Pure interface; could be implemented in TS for Kafka path |
| `RedisStreamBackend` | SIDECAR | Redis client wrapping — keep Python |

The existing `SEAMS_ONLY` classification from `skillforge-integration-plan.md` still holds. E-46 adds one new natural seam: the `/v1/events/` REST surface, which SkillForge can call via HTTP without any Python port.
