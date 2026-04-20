# tests/test_retrieval/test_hybrid_with_embeddings.py
"""RecallPipeline vector-search + RRF-fusion tests — T-130 / T-131.

Covers `apply_vector_search()` and its interaction with the existing
`rrf_fuse()` method. Embedding backend is mocked so these tests are
fast, hermetic, and do not require sentence-transformers.

Backlog: T-130 (S-43).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from depthfusion.retrieval.hybrid import (
    PipelineMode,
    RecallPipeline,
    _cosine_similarity,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _mk_blocks(snippets: list[str]) -> list[dict]:
    return [
        {"chunk_id": f"c{i}", "snippet": s, "score": 1.0}
        for i, s in enumerate(snippets)
    ]


def _mk_backend(query_vec: list[float], doc_vecs: list[list[float]]) -> MagicMock:
    """Create a mock backend whose embed() returns [query_vec, *doc_vecs]."""
    backend = MagicMock()
    backend.embed.return_value = [query_vec, *doc_vecs]
    return backend


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors_return_one(self):
        assert _cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 1.0

    def test_orthogonal_vectors_return_zero(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite_vectors_return_minus_one(self):
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0

    def test_empty_vectors_return_zero(self):
        assert _cosine_similarity([], [1.0]) == 0.0
        assert _cosine_similarity([1.0], []) == 0.0

    def test_length_mismatch_returns_zero(self):
        """Graceful degradation — no IndexError raised."""
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_zero_vector_returns_zero(self):
        """Avoid div-by-zero on zero-norm input."""
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# apply_vector_search
# ---------------------------------------------------------------------------

class TestApplyVectorSearch:
    def test_empty_blocks_returns_empty(self):
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        backend = MagicMock()
        assert p.apply_vector_search("q", [], backend=backend) == []
        backend.embed.assert_not_called()

    def test_ranks_blocks_by_cosine_similarity(self):
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        # query is aligned with vector of doc c1; less aligned with c0
        query_vec = [1.0, 0.0, 0.0]
        doc_vecs = [
            [0.5, 0.5, 0.5],  # c0 — less aligned
            [0.9, 0.1, 0.0],  # c1 — most aligned
            [0.0, 1.0, 0.0],  # c2 — orthogonal
        ]
        blocks = _mk_blocks(["a", "b", "c"])
        backend = _mk_backend(query_vec, doc_vecs)

        result = p.apply_vector_search("query", blocks, backend=backend)

        assert [b["chunk_id"] for b in result] == ["c1", "c0", "c2"]

    def test_attaches_vector_score_to_blocks(self):
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        backend = _mk_backend([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]])
        blocks = _mk_blocks(["x", "y"])
        result = p.apply_vector_search("query", blocks, backend=backend)
        assert all("vector_score" in b for b in result)
        assert result[0]["vector_score"] == 1.0

    def test_preserves_original_block_fields(self):
        """Vector search must not drop snippet/score/etc — only add vector_score."""
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        backend = _mk_backend([1.0], [[1.0]])
        blocks = [{"chunk_id": "c0", "snippet": "text", "score": 42.0, "source": "memory"}]
        result = p.apply_vector_search("q", blocks, backend=backend)
        assert result[0]["snippet"] == "text"
        assert result[0]["score"] == 42.0
        assert result[0]["source"] == "memory"

    def test_top_k_applied_after_ranking(self):
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        backend = _mk_backend([1.0], [[1.0], [0.5], [0.1]])
        blocks = _mk_blocks(["a", "b", "c"])
        result = p.apply_vector_search("q", blocks, top_k=2, backend=backend)
        assert len(result) == 2

    def test_none_from_backend_returns_empty(self):
        """NullBackend and missing sentence-transformers both surface as None."""
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        backend = MagicMock()
        backend.embed.return_value = None
        blocks = _mk_blocks(["a", "b"])
        assert p.apply_vector_search("q", blocks, backend=backend) == []

    def test_length_mismatch_returns_empty(self):
        """Defensive: if backend returns wrong number of vectors, degrade."""
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        backend = MagicMock()
        backend.embed.return_value = [[1.0]]  # only 1 vec for 1 query + 2 docs
        blocks = _mk_blocks(["a", "b"])
        assert p.apply_vector_search("q", blocks, backend=backend) == []

    def test_backend_exception_returns_empty(self):
        """Exceptions in embed() must never propagate to the retrieval path."""
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        backend = MagicMock()
        backend.embed.side_effect = RuntimeError("model died")
        blocks = _mk_blocks(["a"])
        assert p.apply_vector_search("q", blocks, backend=backend) == []

    def test_resolves_backend_from_factory_when_none_passed(self, monkeypatch):
        """When backend=None, apply_vector_search calls get_backend('embedding').

        The source `from depthfusion.backends.factory import get_backend`
        happens inside the function body (not at module import time), so
        patching the factory module attribute is correct — but we patch
        at the source module to be robust against future import hoisting.
        """
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        fake_backend = _mk_backend([1.0], [[1.0]])
        # Patch at the factory source (authoritative location). The
        # `from ... import get_backend` inside apply_vector_search re-reads
        # this attribute on every call, so patching here is effective.
        monkeypatch.setattr(
            "depthfusion.backends.factory.get_backend",
            lambda capability, **kwargs: fake_backend,
        )
        blocks = _mk_blocks(["x"])
        result = p.apply_vector_search("q", blocks)
        assert len(result) == 1
        assert result[0]["vector_score"] == 1.0


# ---------------------------------------------------------------------------
# Integration with rrf_fuse (T-130 AC — fusion is end-to-end usable)
# ---------------------------------------------------------------------------

class TestVectorSearchFusesWithBM25:
    def test_rrf_fuse_combines_bm25_and_vector_results(self):
        """End-to-end: BM25 top results + vector top results → RRF merge."""
        p = RecallPipeline(mode=PipelineMode.VPS_TIER2)
        # BM25 ranks c0 > c1
        bm25 = [
            {"chunk_id": "c0", "score": 10.0},
            {"chunk_id": "c1", "score": 5.0},
        ]
        # Vector search ranks c1 > c2
        blocks = _mk_blocks(["a", "b", "c"])
        backend = _mk_backend([1.0, 0.0], [
            [0.0, 1.0],  # c0 — orthogonal (cos=0)
            [1.0, 0.0],  # c1 — aligned (cos=1)
            [0.5, 0.5],  # c2 — partial
        ])
        vector = p.apply_vector_search("q", blocks, backend=backend)
        fused = p.rrf_fuse(bm25, vector, k=60)

        # Both c0 and c1 appear in both lists, so they should be top-ranked.
        chunk_ids = [b["chunk_id"] for b in fused]
        assert "c0" in chunk_ids
        assert "c1" in chunk_ids
        # c1 is in both bm25 (rank 2) AND top of vector → should outrank c2
        assert chunk_ids.index("c1") < chunk_ids.index("c2")

    def test_empty_vector_results_degrade_to_bm25_only(self):
        """When embedding is unavailable, rrf_fuse returns bm25_results unchanged."""
        p = RecallPipeline(mode=PipelineMode.LOCAL)
        bm25 = [{"chunk_id": "a", "score": 10.0}]
        backend = MagicMock()
        backend.embed.return_value = None
        vector = p.apply_vector_search("q", _mk_blocks(["x"]), backend=backend)
        fused = p.rrf_fuse(bm25, vector)
        assert fused == bm25
