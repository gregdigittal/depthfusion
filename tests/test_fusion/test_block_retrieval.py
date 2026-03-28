"""Tests for fusion/block_retrieval.py — BlockIndex with k-means clustering."""
from __future__ import annotations

import math
import random

import pytest

from depthfusion.core.types import SessionBlock
from depthfusion.fusion.block_retrieval import BlockIndex


def make_block(session_id: str, block_index: int, embedding: list[float]) -> SessionBlock:
    return SessionBlock(
        session_id=session_id,
        block_index=block_index,
        content=f"block {block_index}",
        tags=[],
        relevance_score=0.5,
        embedding=embedding,
    )


def random_unit_vec(dim: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


class TestBlockIndex:
    def test_is_fitted_false_before_fit(self):
        idx = BlockIndex(n_clusters=3)
        assert idx.is_fitted() is False

    def test_is_fitted_true_after_fit(self):
        blocks = [make_block("s1", i, random_unit_vec(4, i)) for i in range(10)]
        idx = BlockIndex(n_clusters=3)
        idx.fit(blocks)
        assert idx.is_fitted() is True

    def test_fit_20_blocks_succeeds(self):
        blocks = [make_block("s1", i, random_unit_vec(8, i)) for i in range(20)]
        idx = BlockIndex(n_clusters=10)
        idx.fit(blocks)
        assert idx.is_fitted()

    def test_query_returns_top_k_blocks(self):
        blocks = [make_block("s1", i, random_unit_vec(4, i)) for i in range(20)]
        idx = BlockIndex(n_clusters=5)
        idx.fit(blocks)
        query = random_unit_vec(4, 99)
        result = idx.query(query, top_k=3)
        assert len(result) == 3

    def test_query_blocks_sorted_by_similarity_descending(self):
        """Returned blocks are sorted by cosine similarity to query, highest first."""
        # Create blocks with distinct, known embeddings
        blocks = [
            make_block("s1", 0, [1.0, 0.0, 0.0, 0.0]),
            make_block("s1", 1, [0.0, 1.0, 0.0, 0.0]),
            make_block("s1", 2, [0.0, 0.0, 1.0, 0.0]),
            make_block("s1", 3, [0.0, 0.0, 0.0, 1.0]),
        ]
        idx = BlockIndex(n_clusters=4)
        idx.fit(blocks)
        query = [1.0, 0.0, 0.0, 0.0]  # perfectly aligned with block 0
        result = idx.query(query, top_k=4)
        # Verify order: first result should be most similar to query
        # We verify sorted order by checking similarities are non-increasing
        from depthfusion.core.scoring import cosine_similarity
        sims = [cosine_similarity(query, b.embedding or [0.0] * 4) for b in result]
        assert sims == sorted(sims, reverse=True)

    def test_n_clusters_greater_than_n_blocks_clips(self):
        """If n_clusters > len(blocks), use len(blocks) clusters."""
        blocks = [make_block("s1", i, random_unit_vec(4, i)) for i in range(3)]
        idx = BlockIndex(n_clusters=10)
        idx.fit(blocks)  # should not raise
        assert idx.is_fitted()

    def test_query_on_unfitted_index_raises_runtime_error(self):
        idx = BlockIndex(n_clusters=3)
        with pytest.raises(RuntimeError, match="not fitted"):
            idx.query([1.0, 0.0], top_k=2)

    def test_query_top_k_larger_than_n_blocks_returns_all_blocks(self):
        """top_k >= total blocks returns all blocks (capped at available blocks)."""
        blocks = [make_block("s1", i, random_unit_vec(4, i)) for i in range(5)]
        idx = BlockIndex(n_clusters=3)
        idx.fit(blocks)
        result = idx.query(random_unit_vec(4, 42), top_k=10)
        assert len(result) == 5  # all blocks returned when top_k exceeds total
