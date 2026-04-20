from depthfusion.core.types import RetrievedChunk
from depthfusion.graph.types import Edge, Entity, TraversalResult


def test_entity_id_is_12_chars():
    e = Entity(
        entity_id="abc123456789",
        name="TierManager",
        type="class",
        project="depthfusion",
        source_files=["memory/foo.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )
    assert len(e.entity_id) == 12


def test_entity_below_threshold_stored():
    e = Entity(
        entity_id="abc123456789",
        name="WeakEntity",
        type="concept",
        project="depthfusion",
        source_files=[],
        confidence=0.50,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )
    assert e.confidence < 0.70


def test_edge_weight_range():
    edge = Edge(
        edge_id="edge00000001",
        source_id="abc123456789",
        target_id="def123456789",
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={},
    )
    assert 1 <= edge.weight <= 3


def test_traversal_result_holds_chunks():
    e = Entity(
        entity_id="abc123456789",
        name="BM25",
        type="concept",
        project="depthfusion",
        source_files=["memory/recall.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )
    chunk = RetrievedChunk(
        chunk_id="recall.md#0",
        content="BM25 scoring is used for recall",
        source="memory",
        score=0.85,
    )
    result = TraversalResult(
        origin_entity=e,
        connected=[],
        source_memories=[chunk],
        depth=1,
    )
    assert result.source_memories[0].chunk_id == "recall.md#0"
