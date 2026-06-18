"""T-562: ACL write-path enforcement tests.

Every store must reject writes where acl_allow is missing or empty.
Raises ValueError("acl_allow is required") — never silently writes.

Stores covered:
  - MemoryStore (via memory.extra["acl_allow"])
  - ChromaDBStore (via metadata["acl_allow"])
  - EventLog (via event acl_allow field / to_dict)
  - GraphStore (JSONGraphStore / SQLiteGraphStore via entity/edge acl_allow metadata)
"""
from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------
from depthfusion.storage.memory_store import MemoryStore
from depthfusion.core.memory_object import MemoryObject, MemoryType


def _make_memory(acl_allow=None, include_acl=True) -> MemoryObject:
    extra: dict = {}
    if include_acl and acl_allow is not None:
        extra["acl_allow"] = acl_allow
    elif include_acl:
        # default: stamped correctly
        extra["acl_allow"] = ["greg"]
    return MemoryObject(
        id=str(uuid.uuid4()),
        project_id="test-project",
        type=MemoryType.SEMANTIC,
        content="Test content",
        extra=extra,
    )


class TestMemoryStoreACL:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store = MemoryStore(Path(self._tmp.name) / "test.db")

    def teardown_method(self):
        self._tmp.cleanup()

    def test_upsert_with_valid_acl_succeeds(self):
        mem = _make_memory(acl_allow=["greg"])
        self._store.upsert(mem)  # should not raise

    def test_upsert_missing_acl_raises(self):
        mem = _make_memory(include_acl=False)
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert(mem)

    def test_upsert_empty_acl_raises(self):
        mem = _make_memory(acl_allow=[])
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert(mem)

    def test_upsert_none_acl_raises(self):
        mem = _make_memory(acl_allow=None, include_acl=True)
        # extra["acl_allow"] = None
        mem.extra["acl_allow"] = None
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert(mem)

    def test_upsert_multi_principal_acl_succeeds(self):
        mem = _make_memory(acl_allow=["greg", "group:admins"])
        self._store.upsert(mem)  # should not raise


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------
from depthfusion.storage.event_log import EventLog
from depthfusion.core.memory import MemoryEvent, MemoryEventType


def _make_event(acl_allow=None, include_acl=True) -> MemoryEvent:
    extra: dict = {}
    if include_acl and acl_allow is not None:
        extra["acl_allow"] = acl_allow
    elif include_acl:
        extra["acl_allow"] = ["greg"]
    return MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=str(uuid.uuid4()),
        event_type=MemoryEventType.CREATED,
        project_id="test-project",
        payload={"extra": extra},
        actor="test",
        timestamp=datetime.now(timezone.utc),
    )


class TestEventLogACL:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._log = EventLog(Path(self._tmp.name) / "events.jsonl")

    def teardown_method(self):
        self._tmp.cleanup()

    def test_append_with_valid_acl_succeeds(self):
        event = _make_event(acl_allow=["greg"])
        result = self._log.append(event)
        assert result is True

    def test_append_missing_acl_raises(self):
        event = _make_event(include_acl=False)
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._log.append(event)

    def test_append_empty_acl_raises(self):
        event = _make_event(acl_allow=[])
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._log.append(event)


# ---------------------------------------------------------------------------
# GraphStore (JSONGraphStore and SQLiteGraphStore)
# ---------------------------------------------------------------------------
from depthfusion.graph.store import JSONGraphStore, SQLiteGraphStore
from depthfusion.graph.types import Entity, Edge


def _make_entity(acl_allow=None, include_acl=True) -> Entity:
    metadata: dict = {}
    if include_acl and acl_allow is not None:
        metadata["acl_allow"] = acl_allow
    elif include_acl:
        metadata["acl_allow"] = ["greg"]
    return Entity(
        entity_id=str(uuid.uuid4())[:12],
        name="TestEntity",
        type="concept",
        project="test",
        source_files=[],
        confidence=1.0,
        first_seen=datetime.now(timezone.utc).isoformat(),
        metadata=metadata,
    )


def _make_edge(acl_allow=None, include_acl=True) -> Edge:
    metadata: dict = {}
    if include_acl and acl_allow is not None:
        metadata["acl_allow"] = acl_allow
    elif include_acl:
        metadata["acl_allow"] = ["greg"]
    return Edge(
        edge_id=str(uuid.uuid4()),
        source_id="src",
        target_id="tgt",
        relationship="CO_OCCURS",
        weight=1.0,
        signals=[],
        metadata=metadata,
    )


class TestJSONGraphStoreACL:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store = JSONGraphStore(path=Path(self._tmp.name) / "graph.json")

    def teardown_method(self):
        self._tmp.cleanup()

    def test_upsert_entity_with_valid_acl_succeeds(self):
        self._store.upsert_entity(_make_entity(acl_allow=["greg"]))

    def test_upsert_entity_missing_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_entity(_make_entity(include_acl=False))

    def test_upsert_entity_empty_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_entity(_make_entity(acl_allow=[]))

    def test_upsert_edge_with_valid_acl_succeeds(self):
        self._store.upsert_edge(_make_edge(acl_allow=["greg"]))

    def test_upsert_edge_missing_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_edge(_make_edge(include_acl=False))

    def test_upsert_edge_empty_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_edge(_make_edge(acl_allow=[]))


class TestSQLiteGraphStoreACL:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store = SQLiteGraphStore(path=Path(self._tmp.name) / "graph.db")

    def teardown_method(self):
        self._tmp.cleanup()

    def test_upsert_entity_with_valid_acl_succeeds(self):
        self._store.upsert_entity(_make_entity(acl_allow=["greg"]))

    def test_upsert_entity_missing_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_entity(_make_entity(include_acl=False))

    def test_upsert_entity_empty_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_entity(_make_entity(acl_allow=[]))

    def test_upsert_edge_with_valid_acl_succeeds(self):
        self._store.upsert_edge(_make_edge(acl_allow=["greg"]))

    def test_upsert_edge_missing_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_edge(_make_edge(include_acl=False))

    def test_upsert_edge_empty_acl_raises(self):
        with pytest.raises(ValueError, match="acl_allow is required"):
            self._store.upsert_edge(_make_edge(acl_allow=[]))
