
from depthfusion.core.memory_object import (
    MemoryObject,
    MemoryStatus,
    MemoryType,
)


def make_memory(**kwargs) -> MemoryObject:
    defaults = dict(
        id="mem-001",
        project_id="proj-test",
        type=MemoryType.SEMANTIC,
        content="Foo is bar",
        summary="Foo = bar",
        status=MemoryStatus.ACTIVE,
    )
    defaults.update(kwargs)
    return MemoryObject(**defaults)


def test_memory_types_enum():
    assert MemoryType.DECISION == "decision"
    assert MemoryType.SEMANTIC == "semantic"
    assert MemoryType.OPERATIONAL == "operational"
    assert MemoryType.PROCEDURAL == "procedural"
    assert MemoryType.EPISODIC == "episodic"
    assert MemoryType.SOCIAL == "social"
    assert MemoryType.TEMPORAL == "temporal"


def test_memory_status_enum():
    assert MemoryStatus.ACTIVE == "active"
    assert MemoryStatus.STALE == "stale"
    assert MemoryStatus.DISPUTED == "disputed"
    assert MemoryStatus.SUPERSEDED == "superseded"
    assert MemoryStatus.ARCHIVED == "archived"


def test_memory_object_construction():
    m = make_memory()
    assert m.id == "mem-001"
    assert m.type == MemoryType.SEMANTIC
    assert m.status == MemoryStatus.ACTIVE


def test_memory_object_serialization():
    m = make_memory()
    d = m.to_dict()
    assert d["id"] == "mem-001"
    assert d["type"] == "semantic"
    m2 = MemoryObject.from_dict(d)
    assert m2.id == m.id
    assert m2.type == m.type
    assert m2.status == m.status


def test_pinned_flag():
    m = make_memory()
    assert m.pinned is False
    m2 = make_memory(pinned=True)
    assert m2.pinned is True
