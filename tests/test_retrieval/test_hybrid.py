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
