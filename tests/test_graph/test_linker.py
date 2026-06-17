# tests/test_graph/test_linker.py
from unittest.mock import MagicMock

import pytest

from depthfusion.graph.linker import (
    CoOccurrenceLinker,
    EntityDeduplicator,
    HaikuLinker,
    TemporalLinker,
    make_edge_id,
    propagate_acl_from_entities,
    propagate_edge_acl,
)
from depthfusion.graph.types import Edge, Entity


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


def _mock_linker_backend(response_text: str, healthy: bool = True):
    """Build a mock LLMBackend whose `complete()` returns the given text.

    Sets `.name` to something other than "null" so HaikuLinker's
    is_available() check (which excludes NullBackend via name) passes.
    """
    mock = MagicMock()
    mock.healthy.return_value = healthy
    mock.name = "haiku"
    mock.complete.return_value = response_text
    return mock


def _make_entity_pair():
    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.types import Entity
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    return a, b


def test_haiku_linker_returns_typed_edge():
    backend = _mock_linker_backend('{"relationship": "DEPENDS_ON"}')
    linker = HaikuLinker(backend=backend)
    a, b = _make_entity_pair()

    edge = linker.infer_relationship(a, b, context="A depends on B for storage")
    assert edge is not None
    assert edge.relationship == "DEPENDS_ON"
    assert "haiku" in edge.signals


def test_haiku_linker_returns_none_when_unavailable():
    """Unhealthy backend → None (no edge inferred)."""
    backend = _mock_linker_backend("", healthy=False)
    linker = HaikuLinker(backend=backend)
    a, b = _make_entity_pair()
    assert linker.infer_relationship(a, b, context="x") is None


def test_haiku_linker_returns_none_with_null_backend():
    """Factory-resolved NullBackend (no key present) → is_available False → None.
    This also covers the C2 fix: no bare anthropic.Anthropic() call ever occurs
    because the factory routes through the backend interface.
    """
    from depthfusion.backends.null import NullBackend
    linker = HaikuLinker(backend=NullBackend())
    a, b = _make_entity_pair()
    assert linker.infer_relationship(a, b, context="x") is None


def test_haiku_linker_handles_invalid_relationship():
    backend = _mock_linker_backend('{"relationship": "INVENTED_TYPE"}')
    linker = HaikuLinker(backend=backend)
    a, b = _make_entity_pair()

    # Invalid relationship type → None (dropped by _HAIKU_VALID_RELATIONSHIPS filter)
    assert linker.infer_relationship(a, b, context="x") is None


def test_haiku_linker_handles_malformed_json():
    backend = _mock_linker_backend("not json at all")
    linker = HaikuLinker(backend=backend)
    a, b = _make_entity_pair()
    assert linker.infer_relationship(a, b, context="x") is None


def test_haiku_linker_handles_empty_response():
    backend = _mock_linker_backend("")
    linker = HaikuLinker(backend=backend)
    a, b = _make_entity_pair()
    assert linker.infer_relationship(a, b, context="x") is None


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


# ---------------------------------------------------------------------------
# T-619: ACL inheritance / propagation onto edges
# ---------------------------------------------------------------------------


def _doc_entity(name: str, eid: str, acl: list[str]) -> Entity:
    return Entity(
        entity_id=eid, name=name, type="class", project="depthfusion",
        source_files=["docs/contract.md"], confidence=1.0,
        first_seen="2026-03-28T00:00:00", metadata={"acl_allow": list(acl)},
    )


def test_propagate_edge_acl_stamps_metadata():
    edge = Edge(
        edge_id="e1", source_id="s", target_id="t", relationship="CO_OCCURS",
        weight=1.0, signals=["co_occurrence"], metadata={},
    )
    out = propagate_edge_acl(edge, ["acme-corp", "legal"])
    assert out.metadata["acl_allow"] == ["acme-corp", "legal"]


def test_propagate_edge_acl_rejects_empty():
    edge = Edge(
        edge_id="e1", source_id="s", target_id="t", relationship="CO_OCCURS",
        weight=1.0, signals=["co_occurrence"], metadata={},
    )
    with pytest.raises(ValueError, match="acl_allow is required"):
        propagate_edge_acl(edge, [])


def test_edges_inherit_source_document_acl():
    """CO_OCCURS edges built from document entities inherit the doc's ACL."""
    a = _doc_entity("Acme", "aaa111111111", ["acme-corp"])
    b = _doc_entity("Beta", "bbb222222222", ["acme-corp"])
    edges = CoOccurrenceLinker().link([a, b])
    propagate_acl_from_entities(edges, [a, b])
    assert edges
    for e in edges:
        assert e.metadata["acl_allow"] == ["acme-corp"]


def test_propagated_edge_acl_satisfies_store_validation():
    from depthfusion.graph.store import _validate_graph_acl
    a = _doc_entity("Acme", "aaa111111111", ["acme-corp"])
    b = _doc_entity("Beta", "bbb222222222", ["acme-corp"])
    edges = CoOccurrenceLinker().link([a, b])
    propagate_acl_from_entities(edges, [a, b])
    for e in edges:
        _validate_graph_acl(e.metadata.get("acl_allow"))  # must not raise


def test_edge_without_source_acl_still_raises_on_validation():
    """Negative case: an edge whose source entity has no ACL is left empty,
    so the store's required-ACL rule rejects it (fail-closed)."""
    from depthfusion.graph.store import _validate_graph_acl
    a = Entity(
        entity_id="aaa111111111", name="Acme", type="class", project="depthfusion",
        source_files=[], confidence=1.0, first_seen="2026-03-28T00:00:00",
        metadata={},  # no acl_allow
    )
    b = _doc_entity("Beta", "bbb222222222", ["acme-corp"])
    edges = CoOccurrenceLinker().link([a, b])  # source is a (no ACL)
    propagate_acl_from_entities(edges, [a, b])
    with pytest.raises(ValueError, match="acl_allow is required"):
        _validate_graph_acl(edges[0].metadata.get("acl_allow"))


# ---------------------------------------------------------------------------
# T-620: entity-linker deduplication (near-duplicate merge)
# ---------------------------------------------------------------------------


def _ent(name: str, etype: str = "concept", conf: float = 0.85,
         project: str = "depthfusion", source: str = "docs/a.md",
         acl: list[str] | None = None) -> Entity:
    from depthfusion.graph.extractor import make_entity_id
    return Entity(
        entity_id=make_entity_id(name, etype, project), name=name, type=etype,
        project=project, source_files=[source], confidence=conf,
        first_seen="2026-03-28T00:00:00",
        metadata={"acl_allow": list(acl) if acl else [project]},
    )


def test_dedup_merges_near_duplicate_same_name_and_type():
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("Acme Corp"), _ent("acme corp"), _ent("ACME  Corp.")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 1


def test_dedup_keeps_distinct_below_threshold():
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("Acme Corporation"), _ent("Beta Industries")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 2


def test_dedup_does_not_merge_across_types():
    """Same normalized name but different type → distinct nodes."""
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("parser", etype="concept"), _ent("parser", etype="function")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 2


def test_dedup_unions_source_files_on_merge():
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", source="docs/a.md")
    b = _ent("acme corp", source="docs/b.md")
    merged = dedup.deduplicate([a, b])
    assert len(merged) == 1
    assert set(merged[0].source_files) == {"docs/a.md", "docs/b.md"}


def test_dedup_keeps_higher_confidence_on_merge():
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", conf=0.70)
    b = _ent("acme corp", conf=0.95)
    merged = dedup.deduplicate([a, b])
    assert merged[0].confidence == 0.95


def test_dedup_intersects_acls_on_merge():
    """Merged node is fail-closed: ACL is intersection, not union.

    ACL policy is INTERSECTION (never widen access beyond what every
    contributing document independently allowed).  A principal who could
    only read one of the two source documents does NOT gain access to the
    merged node.  See EntityDeduplicator docstring for the full rationale.
    """
    dedup = EntityDeduplicator(threshold=0.85)
    # shared principal appears in both → survives intersection
    a = _ent("Acme Corp", acl=["acme-corp", "shared-team"])
    b = _ent("acme corp", acl=["legal-team", "shared-team"])
    merged = dedup.deduplicate([a, b])
    assert len(merged) == 1
    # only "shared-team" is in both ACLs → intersection
    assert set(merged[0].metadata["acl_allow"]) == {"shared-team"}


def test_dedup_acl_intersection_disjoint_is_empty():
    """Disjoint ACLs → empty intersection (no principal can see all sources)."""
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", acl=["acme-corp"])
    b = _ent("acme corp", acl=["legal-team"])
    merged = dedup.deduplicate([a, b])
    assert len(merged) == 1
    assert merged[0].metadata["acl_allow"] == []


def test_dedup_threshold_is_tunable():
    """A stricter threshold keeps partial-overlap names distinct."""
    loose = EntityDeduplicator(threshold=0.4)
    strict = EntityDeduplicator(threshold=0.95)
    ents = [_ent("Acme Corporation Ltd"), _ent("Acme Corporation")]
    assert len(loose.deduplicate(ents)) == 1   # 2/3 token overlap >= 0.4
    assert len(strict.deduplicate(ents)) == 2   # < 0.95 → distinct


def test_dedup_empty_input():
    assert EntityDeduplicator().deduplicate([]) == []
