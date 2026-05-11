import pytest
from pathlib import Path

from depthfusion.core.memory_object import MemoryObject, MemoryStatus, MemoryType
from depthfusion.storage.memory_store import MemoryStore


def make_memory(
    id="mem-001",
    project="proj-test",
    type=MemoryType.SEMANTIC,
    status=MemoryStatus.ACTIVE,
    pinned=False,
) -> MemoryObject:
    return MemoryObject(
        id=id,
        project_id=project,
        type=type,
        content=f"content for {id}",
        summary="",
        status=status,
        pinned=pinned,
    )


def test_memory_store_upsert_and_get(tmp_path):
    store = MemoryStore(tmp_path / "memories.db")
    m = make_memory()
    store.upsert(m)
    retrieved = store.get("mem-001")
    assert retrieved is not None
    assert retrieved.id == "mem-001"
    assert retrieved.type == MemoryType.SEMANTIC


def test_memory_store_update(tmp_path):
    store = MemoryStore(tmp_path / "memories.db")
    m = make_memory()
    store.upsert(m)
    m.status = MemoryStatus.STALE
    m.summary = "updated"
    store.upsert(m)
    retrieved = store.get("mem-001")
    assert retrieved.status == MemoryStatus.STALE
    assert retrieved.summary == "updated"


def test_memory_store_query_by_project(tmp_path):
    store = MemoryStore(tmp_path / "memories.db")
    store.upsert(make_memory("m1", "proj-a"))
    store.upsert(make_memory("m2", "proj-b"))
    store.upsert(make_memory("m3", "proj-a"))
    results = store.query(project_id="proj-a")
    assert len(results) == 2
    assert all(r.project_id == "proj-a" for r in results)


def test_memory_store_excludes_archived_by_default(tmp_path):
    store = MemoryStore(tmp_path / "memories.db")
    store.upsert(make_memory("m1", status=MemoryStatus.ACTIVE))
    store.upsert(make_memory("m2", status=MemoryStatus.ARCHIVED))
    results = store.query(project_id="proj-test")
    assert len(results) == 1
    assert results[0].id == "m1"


def test_memory_store_pinned_preserved(tmp_path):
    store = MemoryStore(tmp_path / "memories.db")
    store.upsert(make_memory("m1", pinned=True))
    retrieved = store.get("m1")
    assert retrieved.pinned is True
