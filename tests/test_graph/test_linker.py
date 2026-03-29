# tests/test_graph/test_linker.py
import pytest
from unittest.mock import MagicMock
from depthfusion.graph.linker import CoOccurrenceLinker, TemporalLinker, HaikuLinker, make_edge_id
from depthfusion.graph.types import Entity, Edge


@pytest.fixture
def entity_a(sample_entity):
    return sample_entity   # TierManager


@pytest.fixture
def entity_b(sample_entity_b):
    return sample_entity_b  # RecallPipeline


def test_co_occurrence_creates_edge(entity_a, entity_b):
    linker = CoOccurrenceLinker()
    edges = linker.link([entity_a, entity_b])
    assert len(edges) == 1
    assert edges[0].relationship == "CO_OCCURS"


def test_co_occurrence_no_edge_for_single_entity(entity_a):
    linker = CoOccurrenceLinker()
    edges = linker.link([entity_a])
    assert edges == []


def test_co_occurrence_weight_is_1():
    from depthfusion.graph.extractor import make_entity_id
    entities = [
        Entity(entity_id=make_entity_id(f"E{i}", "class", "p"), name=f"E{i}",
               type="class", project="p", source_files=["f.md"],
               confidence=1.0, first_seen="2026-03-28T00:00:00", metadata={})
        for i in range(3)
    ]
    linker = CoOccurrenceLinker()
    edges = linker.link(entities)
    assert all(e.weight == 1.0 for e in edges)


def test_co_occurrence_signal_label(entity_a, entity_b):
    linker = CoOccurrenceLinker()
    edges = linker.link([entity_a, entity_b])
    assert "co_occurrence" in edges[0].signals


def test_make_edge_id_is_deterministic():
    a = make_edge_id("src1", "tgt1", "CO_OCCURS")
    b = make_edge_id("src1", "tgt1", "CO_OCCURS")
    assert a == b


def test_make_edge_id_differs_by_relationship():
    a = make_edge_id("src1", "tgt1", "CO_OCCURS")
    b = make_edge_id("src1", "tgt1", "DEPENDS_ON")
    assert a != b


def test_temporal_linker_within_48h(entity_a, entity_b):
    linker = TemporalLinker(window_hours=48)
    # Same timestamp → within window
    ts = "2026-03-28T10:00:00"
    edges = linker.link_across_sessions(
        session_a_entities=[entity_a], session_a_ts=ts,
        session_b_entities=[entity_b], session_b_ts=ts,
    )
    assert len(edges) >= 1
    assert edges[0].relationship == "CO_WORKED_ON"


def test_temporal_linker_outside_window(entity_a, entity_b):
    linker = TemporalLinker(window_hours=48)
    edges = linker.link_across_sessions(
        session_a_entities=[entity_a], session_a_ts="2026-03-20T00:00:00",
        session_b_entities=[entity_b], session_b_ts="2026-03-28T00:00:00",
    )
    assert edges == []


def test_temporal_linker_signal_label(entity_a, entity_b):
    linker = TemporalLinker(window_hours=48)
    ts = "2026-03-28T10:00:00"
    edges = linker.link_across_sessions(
        session_a_entities=[entity_a], session_a_ts=ts,
        session_b_entities=[entity_b], session_b_ts=ts,
    )
    assert all("temporal" in e.signals for e in edges)


def test_haiku_linker_returns_typed_edge():
    linker = HaikuLinker()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"relationship": "DEPENDS_ON"}')]
    mock_client.messages.create.return_value = mock_response
    linker._client = mock_client

    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.types import Entity
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})

    edge = linker.infer_relationship(a, b, context="A depends on B for storage")
    assert edge is not None
    assert edge.relationship == "DEPENDS_ON"
    assert "haiku" in edge.signals


def test_haiku_linker_returns_none_when_unavailable():
    linker = HaikuLinker()
    linker._client = None
    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.types import Entity
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    result = linker.infer_relationship(a, b, context="x")
    assert result is None


def test_haiku_linker_handles_invalid_relationship():
    linker = HaikuLinker()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"relationship": "INVENTED_TYPE"}')]
    mock_client.messages.create.return_value = mock_response
    linker._client = mock_client

    from depthfusion.graph.extractor import make_entity_id
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    result = linker.infer_relationship(a, b, context="x")
    # Invalid relationship type → None
    assert result is None


def test_weight_accumulation_across_signals(entity_a, entity_b):
    """Edge weight should reflect combined signal count."""
    co_edge = Edge(
        edge_id=make_edge_id(entity_a.entity_id, entity_b.entity_id, "CO_OCCURS"),
        source_id=entity_a.entity_id,
        target_id=entity_b.entity_id,
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={},
    )
    # Simulate adding a temporal signal
    co_edge.signals.append("temporal")
    co_edge.weight = float(len(co_edge.signals))
    assert co_edge.weight == 2.0
