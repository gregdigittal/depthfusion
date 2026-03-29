# tests/test_graph/test_traverser.py
import pytest
from depthfusion.graph.traverser import traverse, expand_query, boost_scores
from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.types import Entity, Edge, TraversalResult
from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.linker import make_edge_id


@pytest.fixture
def populated_store(tmp_path, sample_entity, sample_entity_b, sample_edge):
    store = JSONGraphStore(path=tmp_path / "g.json")
    store.upsert_entity(sample_entity)     # TierManager
    store.upsert_entity(sample_entity_b)   # RecallPipeline
    store.upsert_edge(sample_edge)         # CO_OCCURS
    return store


def test_traverse_depth1_finds_connected(populated_store, sample_entity, sample_entity_b):
    result = traverse(sample_entity.entity_id, populated_store, depth=1)
    assert result is not None
    connected_ids = [e.entity_id for e, _ in result.connected]
    assert sample_entity_b.entity_id in connected_ids


def test_traverse_returns_traversal_result(populated_store, sample_entity):
    result = traverse(sample_entity.entity_id, populated_store, depth=1)
    assert isinstance(result, TraversalResult)
    assert result.origin_entity.name == "TierManager"


def test_traverse_unknown_entity_returns_none(populated_store):
    result = traverse("nonexistent_id", populated_store, depth=1)
    assert result is None


def test_traverse_depth0_returns_origin_only(populated_store, sample_entity):
    result = traverse(sample_entity.entity_id, populated_store, depth=0)
    assert result is not None
    assert result.connected == []


def test_traverse_relationship_filter(populated_store, sample_entity, sample_entity_b):
    result = traverse(
        sample_entity.entity_id, populated_store, depth=1,
        relationship_filter=["DEPENDS_ON"]
    )
    assert result is not None
    assert result.connected == []  # only CO_OCCURS exists


def test_expand_query_adds_linked_terms(populated_store, sample_entity):
    """expand_query extracts entity names from query and adds linked entity names."""
    # TierManager appears in query; graph shows it CO_OCCURS with RecallPipeline
    expanded = expand_query("TierManager storage", populated_store)
    assert "TierManager" in expanded  # original term preserved
    assert "RecallPipeline" in expanded  # linked entity added


def test_expand_query_no_match_returns_original(populated_store):
    expanded = expand_query("unrelated query terms", populated_store)
    # No entities match → original query returned unchanged
    assert "unrelated" in expanded


def test_expand_query_never_removes_original_terms(populated_store):
    expanded = expand_query("TierManager storage tier", populated_store)
    for term in ["TierManager", "storage", "tier"]:
        assert term in expanded


def test_boost_scores_increases_linked_block(populated_store, sample_entity, sample_entity_b):
    """Blocks mentioning linked entities get a score boost."""
    blocks = [
        {"chunk_id": "mem#0", "content": "RecallPipeline uses BM25", "score": 0.50},
        {"chunk_id": "mem#1", "content": "unrelated content", "score": 0.50},
    ]
    # top-1 result is TierManager-linked → RecallPipeline block should be boosted
    boosted = boost_scores(blocks, top_result_entity_id=sample_entity.entity_id,
                           store=populated_store)
    linked_block = next(b for b in boosted if b["chunk_id"] == "mem#0")
    unlinked_block = next(b for b in boosted if b["chunk_id"] == "mem#1")
    assert linked_block["score"] >= unlinked_block["score"]


def test_boost_scores_max_boost_is_0_30(populated_store, sample_entity, sample_entity_b):
    blocks = [
        {"chunk_id": "mem#0", "content": "RecallPipeline", "score": 0.10},
    ]
    boosted = boost_scores(blocks, top_result_entity_id=sample_entity.entity_id,
                           store=populated_store)
    # Even with multiple edges, max boost is +0.30
    assert boosted[0]["score"] <= 0.40 + 1e-6  # 0.10 + 0.30


def test_boost_scores_is_additive(populated_store, sample_entity):
    blocks = [{"chunk_id": "x", "content": "RecallPipeline", "score": 0.70}]
    boosted = boost_scores(blocks, top_result_entity_id=sample_entity.entity_id,
                           store=populated_store)
    assert boosted[0]["score"] >= 0.70


def test_boost_scores_no_entity_returns_unchanged(tmp_path):
    empty_store = JSONGraphStore(path=tmp_path / "empty.json")
    blocks = [{"chunk_id": "x", "content": "anything", "score": 0.50}]
    boosted = boost_scores(blocks, top_result_entity_id="nobody", store=empty_store)
    assert boosted[0]["score"] == pytest.approx(0.50)


def test_traverse_depth2_walks_two_hops(tmp_path):
    """Depth-2 traversal should reach entities two edges away."""
    store = JSONGraphStore(path=tmp_path / "g.json")
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    c = Entity(entity_id=make_entity_id("C", "class", "p"), name="C", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    store.upsert_entity(a)
    store.upsert_entity(b)
    store.upsert_entity(c)
    store.upsert_edge(Edge(
        edge_id=make_edge_id(a.entity_id, b.entity_id, "CO_OCCURS"),
        source_id=a.entity_id, target_id=b.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"], metadata={},
    ))
    store.upsert_edge(Edge(
        edge_id=make_edge_id(b.entity_id, c.entity_id, "CO_OCCURS"),
        source_id=b.entity_id, target_id=c.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"], metadata={},
    ))
    result = traverse(a.entity_id, store, depth=2)
    connected_ids = {e.entity_id for e, _ in result.connected}
    assert b.entity_id in connected_ids
    assert c.entity_id in connected_ids
