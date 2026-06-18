# Event Graph Fabric — API Reference

All endpoints are mounted under the DepthFusion REST server (default port 7300).
All endpoints require `Authorization: Bearer <DEPTHFUSION_API_TOKEN>` unless the
server is running without a token (loopback-only, dev mode).

---

## Endpoints

### POST /v1/events/publish

Record that an agent has published a set of memories to the shared fabric.
Creates an `event` Entity node (`event_type: AGENT_PUBLISHED`) in the knowledge
graph and notifies any SSE subscribers via the stream backend.

**Request body:**

```json
{
  "agent_id": "agent-a",
  "project_slug": "myproject",
  "memory_refs": ["abc123", "def456"],
  "session_id": "optional-session-id"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_id` | string | yes | Identifies the publishing agent |
| `project_slug` | string | yes | Project namespace; scopes stream channels |
| `memory_refs` | string[] | yes | Entity IDs the agent is publishing |
| `session_id` | string | no | Session identifier for `SAME_SESSION_AS` graph edges |

**Response 200:**

```json
{
  "event_id": "a3f1c8d90b12",
  "indexed": true
}
```

**Response 401:** missing or invalid Bearer token.

---

### GET /v1/events/stream

Server-Sent Events stream of fabric events. Connect once and receive all
`event` Entity JSON objects as they are published to the requested projects.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `projects` | — | Comma-separated project slugs to subscribe to |
| `since_id` | `$` | Redis Stream ID; pass last consumed ID to replay missed events |
| `consumer_id` | — | Identifies this subscriber in consumer group metrics |

**SSE event format:**

```
data: {"entity_id":"a3f1c8d90b12","event_type":"AGENT_PUBLISHED","agent_id":"agent-a","project":"myproject","memory_refs":["abc123"],"first_seen":"2026-05-23T14:00:00+00:00","session_id":null}

```

Each event is a JSON-encoded `EventEntity` metadata dict. The stream stays open
indefinitely; the client reconnects if the server restarts.

**Degradation:** if no Redis backend is configured, this endpoint returns 503
with `{"detail": "stream backend not available — configure DEPTHFUSION_REDIS_URL"}`.
Use `/v1/events/seed` for graph-only access.

---

### GET /v1/events/seed

Return a ranked context bundle for cold-start seeding. Queries the knowledge
graph for recent events in the requested projects, collects their `memory_refs`,
and ranks them by: `score = recall_relevance × recency_decay × log(1 + observer_count + 1)`.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `projects` | — | Comma-separated project slugs |
| `goal` | `""` | Goal phrase used for BM25 keyword scoring against memory names |
| `top_k` | `5` | Maximum number of memories to return |
| `since_hours` | `24` | Recency window for event collection |

**Response 200:**

```json
{
  "memories": [
    {
      "entity_id": "abc123",
      "name": "auth refactor decision",
      "score": 0.84,
      "observer_count": 3,
      "first_seen": "2026-05-23T12:00:00+00:00"
    }
  ],
  "degraded": false,
  "count": 1
}
```

`degraded: true` indicates Redis was unavailable and the bundle was built from
graph-only traversal (no live stream replay). The bundle is still usable.

---

### GET /v1/graph/agent/{agent_id}/trail

Return all `AGENT_PUBLISHED` and `AGENT_RECEIVED` EventEntities for an agent,
sorted ascending by timestamp.

**Path parameters:** `agent_id` — the agent identifier.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `project` | — | Filter by project slug |
| `since` | — | ISO-8601 lower bound (inclusive), e.g. `2026-05-23T00:00:00Z` |
| `until` | — | ISO-8601 upper bound (inclusive) |

**Response 200:**

```json
{
  "trail": [
    {
      "entity_id": "a3f1c8d90b12",
      "event_type": "AGENT_PUBLISHED",
      "memory_refs": ["abc123"],
      "first_seen": "2026-05-23T14:00:00+00:00",
      "project": "myproject",
      "session_id": null
    }
  ],
  "count": 1
}
```

Returns empty list (not 404) if no events match the filter.

---

### GET /v1/graph/memory/{entity_id}/observers

Return all distinct agents that have an `AGENT_RECEIVED` edge to the given memory
entity, with timestamps. Use this to answer "who has seen memory X?"

**Path parameters:** `entity_id` — the memory entity's ID.

**Response 200:**

```json
{
  "observers": [
    {
      "agent_id": "agent-b",
      "timestamp": "2026-05-23T14:01:00+00:00",
      "edge_id": "abc123-recv-agent-b"
    }
  ],
  "count": 1
}
```

**Response 404:** entity not found in the graph.

---

## StreamBackend Protocol

Operators can replace `RedisStreamBackend` with any backend that implements
this async Protocol:

```python
from typing import AsyncIterator, Protocol, runtime_checkable

@runtime_checkable
class StreamBackend(Protocol):
    async def publish(self, channel: str, payload: dict) -> str:
        """Append payload to channel; return stream entry ID."""
        ...

    async def subscribe(
        self,
        channels: list[str],
        since_id: str = "0",
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield (entry_id, payload) tuples in real time."""
        ...

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Return up to count entries starting after since_id."""
        ...

    async def close(self) -> None:
        """Release underlying connections."""
        ...
```

Channel naming convention: `depthfusion:stream:{project_slug}`

Swap in a custom backend via `EventStore(graph=graph, stream=MyBackend())`.
See `docs/fabric/kafka-flink-migration.md` for a `KafkaFlinkBackend` guide.

---

## Error Reference

| Code | Meaning |
|------|---------|
| 200 | Success |
| 401 | Missing or invalid `Authorization: Bearer` header |
| 404 | Entity not found (only `/v1/graph/memory/{id}/observers`) |
| 422 | Invalid date format in `since`/`until` parameters |
| 503 | SSE stream endpoint requires Redis — not configured |
