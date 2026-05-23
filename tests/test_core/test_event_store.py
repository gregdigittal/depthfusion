"""Tests for EventStore, StreamBackend Protocol, and entity_id determinism.

T-483 / S-141 / E-46
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.core.event_store import (
    EventStore,
    InMemoryStreamBackend,
    _event_entity_id,
)
from depthfusion.graph.types import Entity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeGraphBackend:
    """Minimal in-memory GraphBackend stub for isolation."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._edges: list = []

    def upsert_entity(self, entity: Entity) -> None:
        self._entities[entity.entity_id] = entity

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def upsert_edge(self, edge) -> None:
        self._edges.append(edge)

    def get_edges(self, entity_id, relationship_filter=None, as_of=None):
        return [e for e in self._edges if e.source_id == entity_id or e.target_id == entity_id]

    def invalidate_edge(self, edge_id, valid_until):
        return False

    def all_entities(self) -> list[Entity]:
        return list(self._entities.values())

    def node_count(self) -> int:
        return len(self._entities)

    def edge_count(self) -> int:
        return len(self._edges)


def _make_store(with_stream: bool = True) -> tuple[EventStore, _FakeGraphBackend, InMemoryStreamBackend | None]:
    graph = _FakeGraphBackend()
    stream = InMemoryStreamBackend() if with_stream else None
    store = EventStore(graph=graph, stream=stream)
    return store, graph, stream


# ---------------------------------------------------------------------------
# entity_id determinism
# ---------------------------------------------------------------------------

def test_event_entity_id_deterministic() -> None:
    refs = ["abc", "def"]
    id1 = _event_entity_id("agent-a", "publish", "2026-05-23T12:00:00+00:00", refs)
    id2 = _event_entity_id("agent-a", "publish", "2026-05-23T12:00:00+00:00", refs)
    assert id1 == id2
    assert len(id1) == 12


def test_event_entity_id_ref_order_independent() -> None:
    id_sorted = _event_entity_id("agent-a", "publish", "2026-05-23T12:00:00+00:00", ["b", "a"])
    id_other = _event_entity_id("agent-a", "publish", "2026-05-23T12:00:00+00:00", ["a", "b"])
    assert id_sorted == id_other


def test_event_entity_id_unique_per_agent() -> None:
    ts = "2026-05-23T12:00:00+00:00"
    id_a = _event_entity_id("agent-a", "publish", ts, ["x"])
    id_b = _event_entity_id("agent-b", "publish", ts, ["x"])
    assert id_a != id_b


def test_event_entity_id_unique_per_timestamp() -> None:
    id_t1 = _event_entity_id("agent-a", "publish", "2026-05-23T12:00:00+00:00", ["x"])
    id_t2 = _event_entity_id("agent-a", "publish", "2026-05-23T12:00:01+00:00", ["x"])
    assert id_t1 != id_t2


# ---------------------------------------------------------------------------
# EventStore.publish()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_writes_entity_to_graph() -> None:
    store, graph, _ = _make_store()
    event_id = await store.publish(
        agent_id="agent-a",
        project_slug="my-project",
        memory_refs=["mem1", "mem2"],
    )
    assert event_id in graph._entities
    entity = graph._entities[event_id]
    assert entity.type == "event"
    assert entity.metadata["agent_id"] == "agent-a"
    assert entity.metadata["project_slug"] == "my-project"
    assert entity.metadata["memory_refs"] == ["mem1", "mem2"]
    assert entity.metadata["event_type"] == "publish"


@pytest.mark.asyncio
async def test_publish_creates_agent_published_edges() -> None:
    store, graph, _ = _make_store()
    await store.publish(
        agent_id="agent-a",
        project_slug="my-project",
        memory_refs=["mem1", "mem2"],
    )
    relationships = {e.relationship for e in graph._edges}
    assert "AGENT_PUBLISHED" in relationships
    assert len(graph._edges) == 2  # one per memory_ref


@pytest.mark.asyncio
async def test_publish_writes_to_stream() -> None:
    store, graph, stream = _make_store(with_stream=True)
    assert stream is not None
    await store.publish(
        agent_id="agent-a",
        project_slug="my-project",
        memory_refs=["mem1"],
    )
    channel = "depthfusion:stream:my-project"
    assert len(stream._streams.get(channel, [])) == 1


@pytest.mark.asyncio
async def test_publish_with_session_id() -> None:
    store, graph, _ = _make_store()
    await store.publish(
        agent_id="agent-a",
        project_slug="proj",
        memory_refs=["m1"],
        session_id="sess-42",
    )
    entities = list(graph._entities.values())
    assert entities[0].metadata["session_id"] == "sess-42"


# ---------------------------------------------------------------------------
# EventStore.get_recent_events()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_recent_events_returns_event_entities() -> None:
    store, graph, _ = _make_store()
    await store.publish("agent-a", "proj", ["m1"])
    await store.publish("agent-b", "proj", ["m2"])
    events = await store.get_recent_events("proj", since_hours=1.0)
    assert len(events) == 2
    assert all(e.type == "event" for e in events)


@pytest.mark.asyncio
async def test_get_recent_events_filters_by_project() -> None:
    store, graph, _ = _make_store()
    await store.publish("agent-a", "proj-a", ["m1"])
    await store.publish("agent-b", "proj-b", ["m2"])
    events = await store.get_recent_events("proj-a", since_hours=1.0)
    assert len(events) == 1
    assert events[0].metadata["project_slug"] == "proj-a"


@pytest.mark.asyncio
async def test_get_recent_events_filters_by_agent() -> None:
    store, graph, _ = _make_store()
    await store.publish("agent-a", "proj", ["m1"])
    await store.publish("agent-b", "proj", ["m2"])
    events = await store.get_recent_events("proj", agent_id="agent-a")
    assert len(events) == 1
    assert events[0].metadata["agent_id"] == "agent-a"


# ---------------------------------------------------------------------------
# Graceful Redis degradation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_succeeds_when_stream_unavailable() -> None:
    """Graph write must succeed even if stream notification fails."""

    class _FailingStream(InMemoryStreamBackend):
        async def publish(self, channel: str, payload: dict) -> str:
            raise ConnectionError("Redis is down")

    graph = _FakeGraphBackend()
    store = EventStore(graph=graph, stream=_FailingStream())
    event_id = await store.publish("agent-a", "proj", ["m1"])
    assert event_id in graph._entities


@pytest.mark.asyncio
async def test_publish_without_stream_succeeds() -> None:
    store, graph, _ = _make_store(with_stream=False)
    event_id = await store.publish("agent-a", "proj", ["m1"])
    assert event_id in graph._entities


# ---------------------------------------------------------------------------
# InMemoryStreamBackend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_in_memory_stream_round_trip() -> None:
    backend = InMemoryStreamBackend()
    entry_id = await backend.publish("chan-a", {"key": "val"})
    assert entry_id

    entries = await backend.read_since("chan-a", since_id="0", count=10)
    assert len(entries) == 1
    _, payload = entries[0]
    assert payload["key"] == "val"


@pytest.mark.asyncio
async def test_in_memory_stream_subscribe_yields_all() -> None:
    backend = InMemoryStreamBackend()
    await backend.publish("ch", {"x": "1"})
    await backend.publish("ch", {"x": "2"})

    collected = []
    async for entry_id, payload in backend.subscribe(["ch"], since_id="0"):
        collected.append(payload)

    assert len(collected) == 2
