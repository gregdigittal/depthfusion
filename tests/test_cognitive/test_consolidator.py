"""Tests for MemoryConsolidator — Task 11 / E-31 / S-101."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from depthfusion.cognitive.consolidator import MemoryConsolidator
from depthfusion.core.memory_object import MemoryObject, MemoryStatus, MemoryType


def make_mem(
    id: str,
    content: str = "test content",
    status: MemoryStatus = MemoryStatus.ACTIVE,
    pinned: bool = False,
    days_old: int = 1,
) -> MemoryObject:
    now = datetime.now(timezone.utc)
    m = MemoryObject(
        id=id,
        project_id="proj",
        type=MemoryType.SEMANTIC,
        content=content,
        summary="",
        status=status,
        pinned=pinned,
    )
    m.created_at = now - timedelta(days=days_old)
    m.updated_at = now - timedelta(days=days_old)
    return m


def test_consolidator_skips_pinned_for_merge():
    c = MemoryConsolidator(merge_threshold=0.92)
    m1 = make_mem("m1", "Redis is used for caching", pinned=True)
    m2 = make_mem("m2", "Redis used for caching")
    result = c.find_near_duplicates([m1, m2])
    merged_ids = {pair[0] for pair in result.merge_candidates}
    assert "m1" not in merged_ids


def test_consolidator_finds_near_duplicates():
    c = MemoryConsolidator(merge_threshold=0.50)
    m1 = make_mem("m1", "foo bar baz qux apple banana")
    m2 = make_mem("m2", "foo bar baz qux apple orange")
    result = c.find_near_duplicates([m1, m2])
    assert len(result.merge_candidates) >= 1


def test_consolidator_no_false_duplicates():
    c = MemoryConsolidator(merge_threshold=0.92)
    m1 = make_mem("m1", "SQLite is used for the file index")
    m2 = make_mem("m2", "Redis is used for caching layer")
    result = c.find_near_duplicates([m1, m2])
    assert len(result.merge_candidates) == 0


def test_consolidator_archives_stale():
    c = MemoryConsolidator()
    stale = make_mem("m-stale", "old content", status=MemoryStatus.STALE, days_old=180)
    active = make_mem("m-active", "current content", days_old=1)
    result = c.find_archive_candidates([stale, active], stale_days=90)
    archive_ids = [m.id for m in result.archive_candidates]
    assert "m-stale" in archive_ids
    assert "m-active" not in archive_ids


def test_consolidator_skips_pinned_for_archive():
    c = MemoryConsolidator()
    pinned_stale = make_mem(
        "m-pinned", "pinned stale", status=MemoryStatus.STALE, pinned=True, days_old=365
    )
    result = c.find_archive_candidates([pinned_stale], stale_days=30)
    assert len(result.archive_candidates) == 0


def test_consolidator_active_not_archived_regardless_of_age():
    c = MemoryConsolidator()
    old_active = make_mem("m-old", "very old active", status=MemoryStatus.ACTIVE, days_old=500)
    result = c.find_archive_candidates([old_active], stale_days=30)
    assert len(result.archive_candidates) == 0
