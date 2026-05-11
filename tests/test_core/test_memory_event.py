from datetime import datetime, timezone

import pytest

from depthfusion.core.memory import MemoryEvent, MemoryEventType


def test_memory_event_types_defined():
    assert MemoryEventType.CREATED == "memory.created"
    assert MemoryEventType.VERIFIED == "memory.verified"
    assert MemoryEventType.CONTRADICTED == "memory.contradicted"
    assert MemoryEventType.SUPERSEDED == "memory.superseded"
    assert MemoryEventType.MERGED == "memory.merged"
    assert MemoryEventType.DECAYED == "memory.decayed"
    assert MemoryEventType.ARCHIVED == "memory.archived"
    assert MemoryEventType.USED == "memory.used"
    assert MemoryEventType.OUTCOME_RECORDED == "memory.outcome_recorded"


def test_memory_event_frozen():
    e = MemoryEvent(
        event_id="evt-001",
        memory_id="mem-001",
        event_type=MemoryEventType.CREATED,
        project_id="proj-test",
        payload={"content": "test"},
        actor="test-agent",
        timestamp=datetime.now(timezone.utc),
    )
    with pytest.raises((AttributeError, TypeError)):
        e.event_id = "changed"


def test_memory_event_serialization():
    ts = datetime.now(timezone.utc)
    e = MemoryEvent(
        event_id="evt-001",
        memory_id="mem-001",
        event_type=MemoryEventType.CREATED,
        project_id="proj-test",
        payload={"content": "hello"},
        actor="test-agent",
        timestamp=ts,
    )
    d = e.to_dict()
    assert d["event_id"] == "evt-001"
    assert d["event_type"] == "memory.created"
    assert d["schema_version"] == 1
    e2 = MemoryEvent.from_dict(d)
    assert e2.event_id == e.event_id
    assert e2.event_type == e.event_type


def test_memory_event_payload_immutable_copy():
    payload = {"key": "value"}
    e = MemoryEvent(
        event_id="evt-001",
        memory_id="mem-001",
        event_type=MemoryEventType.CREATED,
        project_id="proj-test",
        payload=payload,
        actor="agent",
        timestamp=datetime.now(timezone.utc),
    )
    payload["key"] = "mutated"
    assert e.payload["key"] == "value"
