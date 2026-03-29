# tests/test_retrieval/test_hybrid.py
import pytest
from unittest.mock import MagicMock, patch
from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode


def _make_blocks(n: int) -> list[dict]:
    return [
        {"chunk_id": f"doc{i}", "source": "memory", "score": float(n - i),
         "snippet": f"content about topic {i}"}
        for i in range(n)
    ]


def test_pipeline_mode_local_returns_bm25_only():
    p = RecallPipeline(mode=PipelineMode.LOCAL)
    blocks = _make_blocks(5)
    result = p.apply_reranker(blocks, "query", top_k=3)
    # local mode: no reranker, just top_k slice
    assert len(result) == 3
    assert result[0]["chunk_id"] == "doc0"


def test_pipeline_rrf_fusion_merges_two_ranked_lists():
    p = RecallPipeline(mode=PipelineMode.VPS_TIER2)
    bm25 = [{"chunk_id": "a", "score": 10.0}, {"chunk_id": "b", "score": 5.0}]
    vector = [{"chunk_id": "b", "score": 0.9}, {"chunk_id": "c", "score": 0.8}]
    fused = p.rrf_fuse(bm25, vector, k=60)
    # "b" appears in both lists, should rank higher than "a" or "c" alone
    chunk_ids = [b["chunk_id"] for b in fused]
    assert "b" in chunk_ids
    assert chunk_ids.index("b") <= 1  # b in top 2


def test_pipeline_rrf_handles_empty_vector_list():
    p = RecallPipeline(mode=PipelineMode.VPS_TIER2)
    bm25 = [{"chunk_id": "a", "score": 10.0}]
    fused = p.rrf_fuse(bm25, [], k=60)
    assert fused == bm25


def test_pipeline_rrf_handles_empty_bm25_list():
    p = RecallPipeline(mode=PipelineMode.VPS_TIER2)
    vector = [{"chunk_id": "a", "score": 0.9}]
    fused = p.rrf_fuse([], vector, k=60)
    assert fused == vector


def test_pipeline_apply_reranker_tier1_calls_reranker(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("depthfusion.retrieval.reranker.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="[0, 1, 2]")]
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic.Anthropic.return_value = mock_client
        p = RecallPipeline(mode=PipelineMode.VPS_TIER1)
        blocks = _make_blocks(5)
        result = p.apply_reranker(blocks, "query", top_k=3)
    assert len(result) == 3


def test_pipeline_from_env_local_mode(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    p = RecallPipeline.from_env()
    assert p.mode == PipelineMode.LOCAL


def test_pipeline_from_env_vps_uses_tier_manager(monkeypatch):
    """from_env() in vps mode should query TierManager; skip if storage not yet built."""
    tier_manager_mod = pytest.importorskip("depthfusion.storage.tier_manager")
    Tier = tier_manager_mod.Tier
    TierConfig = tier_manager_mod.TierConfig
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    with patch("depthfusion.retrieval.hybrid.TierManager") as mock_tm:
        mock_tm.return_value.detect_tier.return_value = TierConfig(
            tier=Tier.VPS_TIER1, corpus_size=10, threshold=500,
            sessions_until_promotion=490, mode="vps"
        )
        p = RecallPipeline.from_env()
    assert p.mode == PipelineMode.VPS_TIER1


def test_expand_query_called_when_graph_enabled(tmp_path, monkeypatch):
    """expand_query injects linked terms before BM25 when flag is on."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    from depthfusion.graph.store import JSONGraphStore
    from depthfusion.graph.types import Entity, Edge
    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.linker import make_edge_id

    store_path = tmp_path / "g.json"
    store = JSONGraphStore(path=store_path)
    e1 = Entity(entity_id=make_entity_id("TierManager", "class", "test"),
                name="TierManager", type="class", project="test",
                source_files=["m.md"], confidence=1.0,
                first_seen="2026-03-28T00:00:00", metadata={})
    e2 = Entity(entity_id=make_entity_id("RecallPipeline", "class", "test"),
                name="RecallPipeline", type="class", project="test",
                source_files=["m.md"], confidence=1.0,
                first_seen="2026-03-28T00:00:00", metadata={})
    store.upsert_entity(e1)
    store.upsert_entity(e2)
    store.upsert_edge(Edge(
        edge_id=make_edge_id(e1.entity_id, e2.entity_id, "CO_OCCURS"),
        source_id=e1.entity_id, target_id=e2.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"], metadata={},
    ))

    from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    expanded = pipeline.maybe_expand_query("TierManager storage", graph_store=store)
    assert "RecallPipeline" in expanded


def test_expand_query_skipped_when_graph_disabled(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "false")
    from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    result = pipeline.maybe_expand_query("TierManager storage", graph_store=None)
    assert result == "TierManager storage"


def test_expand_query_no_op_when_store_is_none(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    result = pipeline.maybe_expand_query("any query", graph_store=None)
    assert result == "any query"
