import pytest

from depthfusion.cognitive.contradiction import (
    ConflictSeverity,
    ConflictStatus,
    ContradictionEngine,
)
from depthfusion.core.memory_object import MemoryObject, MemoryType


def make_mem(id: str, content: str, confidence: float = 0.9) -> MemoryObject:
    m = MemoryObject(
        id=id,
        project_id="proj",
        type=MemoryType.SEMANTIC,
        content=content,
    )
    m.confidence.score = confidence
    return m


def test_contradiction_engine_detects_negation():
    engine = ContradictionEngine()
    m1 = make_mem("m1", "Redis is used for caching in prod")
    m2 = make_mem("m2", "Redis is not used in prod")
    conflicts = engine.detect(m1, m2)
    assert len(conflicts) >= 1
    assert conflicts[0].severity in (ConflictSeverity.HIGH, ConflictSeverity.CRITICAL)


def test_contradiction_below_threshold_queued_not_emitted():
    engine = ContradictionEngine(auto_emit_threshold=0.85)
    m1 = make_mem("m1", "Redis is used for caching", confidence=0.5)
    m2 = make_mem("m2", "Redis is not used for caching", confidence=0.5)
    conflicts = engine.detect(m1, m2)
    for c in conflicts:
        assert c.status == ConflictStatus.PENDING_REVIEW


def test_contradiction_above_threshold_auto_emitted():
    engine = ContradictionEngine(auto_emit_threshold=0.85)
    m1 = make_mem("m1", "Redis is used for caching", confidence=0.95)
    m2 = make_mem("m2", "Redis is not used for caching", confidence=0.95)
    conflicts = engine.detect(m1, m2)
    assert any(c.status == ConflictStatus.AUTO_EMITTED for c in conflicts)


def test_pinned_memory_not_contradicted():
    engine = ContradictionEngine()
    m1 = make_mem("m1", "Redis is used for caching")
    m1.pinned = True
    m2 = make_mem("m2", "Redis is not used for caching")
    conflicts = engine.detect(m1, m2)
    for c in conflicts:
        assert c.pinned_winner == "m1"
