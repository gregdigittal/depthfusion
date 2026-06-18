"""Event Graph Fabric — EventStore, StreamBackend Protocol, RedisStreamBackend.

Every memory publish, subscribe, and recall is recorded as a first-class
`event` Entity node in the knowledge graph so any session can later ask
"who knew what, when."

v0.6 / E-46 / S-141
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from asyncio import AbstractEventLoop
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Protocol, runtime_checkable

from depthfusion.graph.store import GraphBackend
from depthfusion.graph.types import Edge, Entity

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# StreamBackend Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class StreamBackend(Protocol):
    """Async streaming transport for event fan-out.

    Mirrors the ``GraphBackend`` Protocol pattern from ``graph/store.py``.
    The canonical v1 implementation is ``RedisStreamBackend`` (Redis Streams).
    Operators can swap in a ``KafkaFlinkBackend`` (v1.5) without touching
    EventStore by implementing this Protocol.
    """

    async def publish(self, channel: str, payload: dict) -> str:
        """Append a payload to the stream channel; return the stream entry ID."""
        ...

    def subscribe(
        self,
        channels: list[str],
        since_id: str = "0",
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield ``(stream_entry_id, payload)`` tuples in real time.

        ``since_id`` enables replay from a past position in the stream
        (pass the last consumed Redis Stream ID, e.g. ``"1716456789123-0"``).
        Pass ``"$"`` to receive only new entries.
        """
        ...

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Return up to ``count`` entries from ``channel`` starting after ``since_id``."""
        ...

    async def close(self) -> None:
        """Release underlying connections."""
        ...


# ---------------------------------------------------------------------------
# InMemoryStreamBackend (used in tests — not a production backend)
# ---------------------------------------------------------------------------

class InMemoryStreamBackend:
    """In-memory stub StreamBackend for unit tests.

    Stores entries in a list per channel so tests can inspect published
    payloads without a Redis dependency.
    """

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, dict]]] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"{int(time.time() * 1000)}-{self._counter}"

    async def publish(self, channel: str, payload: dict) -> str:
        entry_id = self._next_id()
        self._streams.setdefault(channel, []).append((entry_id, payload))
        return entry_id

    async def subscribe(
        self,
        channels: list[str],
        since_id: str = "0",
    ) -> AsyncIterator[tuple[str, dict]]:
        # In tests this generator yields all existing entries then stops.
        for ch in channels:
            for entry_id, payload in self._streams.get(ch, []):
                yield entry_id, payload

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        entries = self._streams.get(channel, [])
        if since_id in ("0", "$"):
            results = entries if since_id == "0" else []
        else:
            results = [e for e in entries if e[0] > since_id]
        return results[:count]

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# RedisStreamBackend
# ---------------------------------------------------------------------------

class RedisStreamBackend:
    """Production StreamBackend backed by Redis Streams (redis.asyncio).

    Channel naming: ``depthfusion:stream:{project_slug}``

    Uses XADD for publishing and XREAD (with consumer groups for fan-out
    to multiple concurrent subscribers) for reading.

    Requires ``redis>=5.0`` (``pip install depthfusion[fabric]``).
    """

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379") -> None:
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "RedisStreamBackend requires redis>=5.0. "
                "Install with: pip install depthfusion[fabric]"
            ) from exc

        self._client = aioredis.from_url(redis_url, decode_responses=True)

    async def publish(self, channel: str, payload: dict) -> str:
        flat: dict[str, str] = {
            k: json.dumps(v) if not isinstance(v, str) else v for k, v in payload.items()
        }
        entry_id: str = await self._client.xadd(channel, flat)  # type: ignore[arg-type]
        return entry_id

    async def subscribe(
        self,
        channels: list[str],
        since_id: str = "$",
    ) -> AsyncIterator[tuple[str, dict]]:
        last_ids: dict = {ch: since_id for ch in channels}
        while True:
            streams = await self._client.xread(
                last_ids,  # type: ignore[arg-type]
                count=100,
                block=1000,
            )
            if not streams:
                continue
            for channel, entries in streams:
                for entry_id, raw in entries:
                    last_ids[channel] = entry_id
                    payload = {
                        k: (json.loads(v) if v and v[0] in ("{", "[", '"') else v)
                        for k, v in raw.items()
                    }
                    yield entry_id, payload

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        raw_entries = await self._client.xrange(channel, min=since_id, count=count)
        results = []
        for entry_id, raw in raw_entries:
            payload = {
                k: (json.loads(v) if v and v[0] in ("{", "[", '"') else v)
                for k, v in raw.items()
            }
            results.append((entry_id, payload))
        return results

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# EventStore
# ---------------------------------------------------------------------------

def _event_entity_id(
    agent_id: str, event_type: str, timestamp_iso: str, memory_refs: list[str]
) -> str:
    """Deterministic, dedup-safe entity_id for event entities.

    Formula: sha256(agent_id + event_type + timestamp_iso + sorted(memory_refs))[:12]
    """
    refs_str = "".join(sorted(memory_refs))
    raw = f"{agent_id}{event_type}{timestamp_iso}{refs_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _graph_lock_path(project_slug: str) -> Path:
    """Sidecar lock file for per-project graph write serialization."""
    lock_dir = Path(os.environ.get("DEPTHFUSION_LOCK_DIR", Path.home() / ".depthfusion" / "locks"))
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f".{project_slug}.graphlock"


class EventStore:
    """Fabric-level event store.

    Writes event Entities to the knowledge graph (authoritative, durable)
    and optionally notifies via a StreamBackend (best-effort, real-time).

    Graph writes are:
    - Serialized per-project via ``file_locking.flock_ex``
    - Run in a thread-pool executor to avoid blocking the asyncio event loop
      (the underlying ``GraphBackend`` is synchronous)
    - Performed with SQLite WAL mode enforced at init time when the backend
      supports it

    Redis stream notifications are best-effort: if the StreamBackend is
    unavailable, a warning is logged and the publish() call still returns
    successfully (with the graph write completed).
    """

    def __init__(
        self,
        graph: GraphBackend,
        stream: StreamBackend | None = None,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        self._graph = graph
        self._stream = stream
        self._loop = loop
        self._enforce_wal()

    @property
    def graph(self) -> GraphBackend:
        """Public read-only access to the underlying graph backend."""
        return self._graph

    def _enforce_wal(self) -> None:
        """Enable SQLite WAL mode if the graph backend is SQLite-backed."""
        import sqlite3 as _sqlite3
        backend = self._graph
        # SQLiteGraphBackend exposes a ``_conn`` attribute (from graph/store.py).
        conn = getattr(backend, "_conn", None)
        if isinstance(conn, _sqlite3.Connection):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
            log.debug("EventStore: SQLite WAL mode enforced")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def publish(
        self,
        agent_id: str,
        project_slug: str,
        memory_refs: list[str],
        event_type: str = "publish",
        session_id: str | None = None,
    ) -> str:
        """Record a publish event in the graph and notify via stream.

        Returns the event entity_id.
        """
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        event_id = _event_entity_id(agent_id, event_type, timestamp_iso, memory_refs)

        entity = Entity(
            entity_id=event_id,
            name=f"{event_type}:{agent_id}:{timestamp_iso}",
            type="event",
            project=project_slug,
            source_files=[],
            confidence=1.0,
            first_seen=timestamp_iso,
            metadata={
                "acl_allow": [project_slug],
                "event_type": event_type,
                "agent_id": agent_id,
                "project_slug": project_slug,
                "memory_refs": memory_refs,
                "session_id": session_id,
            },
        )

        # Graph write (authoritative) — serialize per project
        await self._graph_write(project_slug, entity)

        # Edges: AGENT_PUBLISHED from event → each referenced memory entity
        for ref_id in memory_refs:
            await self._graph_write_edge(project_slug, entity.entity_id, ref_id, "AGENT_PUBLISHED")

        # Stream notification (best-effort)
        await self._stream_publish(project_slug, entity)

        return event_id

    async def publish_memory(
        self,
        content: str,
        agent_id: str,
        project_slug: str,
        event_type: str = "publish",
        session_id: str | None = None,
    ) -> dict:
        """Publish content as a MemoryEntity + EventEntity with content-hash dedup.

        The MemoryEntity is content-addressed: entity_id = sha256(content)[:12].
        Concurrent publishes of identical content converge to 1 MemoryEntity
        because ``upsert_entity`` is idempotent (INSERT OR REPLACE in SQLite).

        Returns: {"memory_id": str, "event_id": str, "deduped": bool}
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        timestamp_iso = datetime.now(timezone.utc).isoformat()

        # Check if MemoryEntity already exists (best-effort dedup signal).
        # Under concurrency, multiple callers may see None simultaneously —
        # the upsert below is still idempotent so only 1 entity ends up in the graph.
        loop = asyncio.get_event_loop()
        existing = await loop.run_in_executor(None, self._graph.get_entity, content_hash)
        deduped = existing is not None

        memory_entity = Entity(
            entity_id=content_hash,
            name=content[:80].replace("\n", " "),
            type="memory",
            project=project_slug,
            source_files=[],
            confidence=1.0,
            first_seen=existing.first_seen if existing else timestamp_iso,
            metadata={
                "acl_allow": [project_slug],
                "content_hash": content_hash,
                "agent_id": agent_id,
            },
        )
        await self._graph_write(project_slug, memory_entity)

        if deduped:
            log.debug(
                "EventStore.publish_memory: content_hash=%s already in graph — dedup hit",
                content_hash,
            )

        # EventEntity — always unique (distinct timestamp per call)
        event_id = _event_entity_id(agent_id, event_type, timestamp_iso, [content_hash])
        event_entity = Entity(
            entity_id=event_id,
            name=f"{event_type}:{agent_id}:{timestamp_iso}",
            type="event",
            project=project_slug,
            source_files=[],
            confidence=1.0,
            first_seen=timestamp_iso,
            metadata={
                "acl_allow": [project_slug],
                "event_type": event_type,
                "agent_id": agent_id,
                "project_slug": project_slug,
                "memory_refs": [content_hash],
                "session_id": session_id,
                "content_hash": content_hash,
            },
        )
        await self._graph_write(project_slug, event_entity)
        await self._graph_write_edge(project_slug, event_id, content_hash, "AGENT_PUBLISHED")
        await self._stream_publish(project_slug, event_entity)

        return {"memory_id": content_hash, "event_id": event_id, "deduped": deduped}

    async def get_recent_events(
        self,
        project_slug: str,
        since_hours: float = 24.0,
        agent_id: str | None = None,
        event_types: list[str] | None = None,
    ) -> list[Entity]:
        """Return recent event entities from the graph matching the filters."""
        loop = asyncio.get_event_loop()
        entities: list[Entity] = await loop.run_in_executor(
            None, self._graph.all_entities
        )
        cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600

        results = []
        for e in entities:
            if e.type != "event":
                continue
            if e.metadata.get("project_slug") != project_slug:
                continue
            try:
                ts = datetime.fromisoformat(e.first_seen).timestamp()
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            if agent_id and e.metadata.get("agent_id") != agent_id:
                continue
            if event_types and e.metadata.get("event_type") not in event_types:
                continue
            results.append(e)

        results.sort(key=lambda x: x.first_seen)
        return results

    async def fabric_seed_bundle(
        self,
        projects: list[str],
        goal: str = "",
        top_k: int = 5,
        since_hours: float = 24.0,
    ) -> dict:
        """Compute a ranked context bundle for fabric_seed mode.

        Ranking: score = recall_relevance × recency_decay × log(1 + observer_count)
        - recall_relevance: BM25 keyword overlap between goal and memory name (0-1)
        - recency_decay: exp(-days_since_first_seen / 7)
        - observer_count: distinct agent_ids with AGENT_RECEIVED edges to the memory

        Falls back to graph-only traversal if Redis unavailable; returns
        ``degraded: True`` in that case.
        """
        import math

        degraded = self._stream is None
        loop = asyncio.get_event_loop()

        # Collect recent event entities for the requested projects
        all_entities: list[Entity] = await loop.run_in_executor(None, self._graph.all_entities)
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - since_hours * 3600

        # Gather unique memory_refs from recent events across requested projects
        memory_ids: set[str] = set()
        for e in all_entities:
            if e.type != "event":
                continue
            if e.metadata.get("project_slug") not in projects:
                continue
            try:
                ts = datetime.fromisoformat(e.first_seen).timestamp()
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            for ref in e.metadata.get("memory_refs", []):
                memory_ids.add(ref)

        # Fetch MemoryEntities and score them
        goal_tokens = set(goal.lower().split()) if goal else set()
        scored: list[tuple[float, Entity]] = []

        for mid in memory_ids:
            entity = await loop.run_in_executor(None, self._graph.get_entity, mid)
            if entity is None:
                continue

            # recency_decay
            try:
                ts = datetime.fromisoformat(entity.first_seen).timestamp()
                days_old = (now - ts) / 86400.0
                recency_decay = math.exp(-days_old / 7.0)
            except (ValueError, TypeError):
                recency_decay = 0.1

            # observer_count via AGENT_RECEIVED edges
            edges = await loop.run_in_executor(
                None,
                lambda: self._graph.get_edges(mid, relationship_filter=["AGENT_RECEIVED"]),
            )
            agent_ids = {e.metadata.get("agent_id", e.source_id) for e in edges}
            observer_count = len(agent_ids)

            # recall_relevance — Jaccard-style token overlap with goal
            if goal_tokens:
                name_tokens = set(entity.name.lower().split())
                overlap = len(goal_tokens & name_tokens)
                recall_relevance = overlap / max(len(goal_tokens | name_tokens), 1)
            else:
                recall_relevance = 1.0

            score = recall_relevance * recency_decay * math.log(1 + observer_count + 1)
            scored.append((score, entity))

        scored.sort(key=lambda x: x[0], reverse=True)
        bundle = [
            {
                "memory_id": e.entity_id,
                "name": e.name,
                "project": e.project,
                "first_seen": e.first_seen,
                "score": round(s, 4),
                "observer_count": len(
                    {ed.metadata.get("agent_id", ed.source_id)
                     for ed in self._graph.get_edges(
                         e.entity_id, relationship_filter=["AGENT_RECEIVED"]
                     )}
                ),
                "metadata": e.metadata,
            }
            for s, e in scored[:top_k]
        ]

        return {
            "bundle": bundle,
            "degraded": degraded,
            "project_count": len(projects),
            "memory_ids_scanned": len(memory_ids),
        }

    async def subscribe_stream(
        self,
        projects: list[str],
        since_id: str = "$",
        consumer_id: str | None = None,
    ) -> AsyncIterator[tuple[str, Entity]]:
        """Yield ``(stream_entry_id, EventEntity)`` from the live stream.

        Requires a configured StreamBackend. Raises ``RuntimeError`` if none
        is configured.
        """
        if self._stream is None:
            raise RuntimeError(
                "EventStore.subscribe_stream requires a StreamBackend. "
                "Configure RedisStreamBackend and pass it to EventStore()."
            )
        channels = [f"depthfusion:stream:{p}" for p in projects]
        async for entry_id, payload in self._stream.subscribe(channels, since_id=since_id):
            entity = self._payload_to_entity(payload)
            if entity is not None:
                yield entry_id, entity

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _graph_write(self, project_slug: str, entity: Entity) -> None:
        """Write entity to graph, serialized per-project via fcntl lock."""
        from depthfusion.core.file_locking import flock_ex, flock_un

        lock_path = _graph_lock_path(project_slug)
        loop = asyncio.get_event_loop()

        def _write() -> None:
            lock_fh = open(lock_path, "a", encoding="utf-8")
            try:
                flock_ex(lock_fh.fileno())
                try:
                    self._graph.upsert_entity(entity)
                finally:
                    flock_un(lock_fh.fileno())
            finally:
                lock_fh.close()

        await loop.run_in_executor(None, _write)

    async def _graph_write_edge(
        self,
        project_slug: str,
        source_id: str,
        target_id: str,
        relationship: str,
    ) -> None:
        """Write a directed edge to the graph, serialized per-project."""
        import hashlib as _hl

        from depthfusion.core.file_locking import flock_ex, flock_un

        edge_id = _hl.sha256(f"{source_id}{relationship}{target_id}".encode()).hexdigest()[:12]
        edge = Edge(
            edge_id=edge_id,
            source_id=source_id,
            target_id=target_id,
            relationship=relationship,
            weight=1.0,
            signals=["event_fabric"],
            adapter_name="event_store",
            source_type="event",
            metadata={"acl_allow": [project_slug]},
        )

        lock_path = _graph_lock_path(project_slug)
        loop = asyncio.get_event_loop()

        def _write() -> None:
            lock_fh = open(lock_path, "a", encoding="utf-8")
            try:
                flock_ex(lock_fh.fileno())
                try:
                    self._graph.upsert_edge(edge)
                finally:
                    flock_un(lock_fh.fileno())
            finally:
                lock_fh.close()

        await loop.run_in_executor(None, _write)

    async def _stream_publish(self, project_slug: str, entity: Entity) -> None:
        """Best-effort stream notification. Logs and continues on failure."""
        if self._stream is None:
            return
        channel = f"depthfusion:stream:{project_slug}"
        payload = {
            "entity_id": entity.entity_id,
            "name": entity.name,
            "type": entity.type,
            "project": entity.project,
            "first_seen": entity.first_seen,
            "metadata": entity.metadata,
        }
        try:
            await self._stream.publish(channel, payload)
        except Exception as exc:
            log.warning(
                "EventStore: stream publish failed (best-effort) — %s: %s",
                type(exc).__name__,
                exc,
            )

    @staticmethod
    def _payload_to_entity(payload: dict) -> Entity | None:
        """Reconstruct an EventEntity from a raw stream payload dict."""
        try:
            metadata = payload.get("metadata", {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            return Entity(
                entity_id=payload["entity_id"],
                name=payload.get("name", ""),
                type=payload.get("type", "event"),
                project=payload.get("project", ""),
                source_files=[],
                confidence=1.0,
                first_seen=payload.get("first_seen", ""),
                metadata=metadata,
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            log.warning("EventStore: malformed stream payload — %s", exc)
            return None
