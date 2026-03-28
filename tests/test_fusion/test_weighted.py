"""Tests for fusion/weighted.py — AttnRes-inspired weighted fusion."""
from __future__ import annotations

from depthfusion.core.types import RetrievedChunk, SessionBlock
from depthfusion.fusion.weighted import attnres_fusion, compute_block_weights


def make_chunk(
    chunk_id: str,
    content: str = "test",
    source: str = "session_file",
    score: float = 0.5,
    rank: int | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        content=content,
        source=source,
        score=score,
        rank=rank,
    )


def make_block(
    session_id: str,
    block_index: int,
    embedding: list[float] | None = None,
) -> SessionBlock:
    return SessionBlock(
        session_id=session_id,
        block_index=block_index,
        content=f"block {block_index}",
        tags=[],
        relevance_score=0.5,
        embedding=embedding,
    )


def unit_vec(dim: int, idx: int) -> list[float]:
    """Return a unit vector with 1.0 at position idx."""
    v = [0.0] * dim
    v[idx] = 1.0
    return v


class TestComputeBlockWeights:
    def test_returns_list_same_length_as_blocks(self):
        query_emb = [1.0, 0.0]
        blocks = [make_block("s1", 0, [1.0, 0.0]), make_block("s1", 1, [0.0, 1.0])]
        weights = compute_block_weights(query_emb, blocks)
        assert len(weights) == 2

    def test_weights_sum_to_one(self):
        query_emb = [1.0, 0.0, 0.0]
        blocks = [
            make_block("s1", 0, [1.0, 0.0, 0.0]),
            make_block("s1", 1, [0.0, 1.0, 0.0]),
            make_block("s1", 2, [0.0, 0.0, 1.0]),
        ]
        weights = compute_block_weights(query_emb, blocks)
        assert abs(sum(weights) - 1.0) < 1e-9

    def test_aligned_block_gets_higher_weight(self):
        """Block whose embedding aligns with query gets higher weight."""
        query_emb = [1.0, 0.0]
        blocks = [
            make_block("s1", 0, [1.0, 0.0]),  # perfectly aligned
            make_block("s1", 1, [0.0, 1.0]),  # orthogonal
        ]
        weights = compute_block_weights(query_emb, blocks)
        assert weights[0] > weights[1]

    def test_empty_blocks_returns_empty(self):
        weights = compute_block_weights([1.0, 0.0], [])
        assert weights == []

    def test_blocks_without_embeddings_handled(self):
        """Blocks with no embedding are treated as zero similarity."""
        query_emb = [1.0, 0.0]
        blocks = [
            make_block("s1", 0, None),  # no embedding
            make_block("s1", 1, [1.0, 0.0]),
        ]
        weights = compute_block_weights(query_emb, blocks)
        # The block with embedding should have higher weight
        assert weights[1] > weights[0]


class TestAttnresFusion:
    def test_returns_chunks_sorted_descending(self):
        chunks = [
            make_chunk("c1", score=0.3),
            make_chunk("c2", score=0.9),
            make_chunk("c3", score=0.6),
        ]
        result = attnres_fusion(chunks)
        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)

    def test_empty_input_returns_empty(self):
        assert attnres_fusion([]) == []

    def test_single_chunk_rank_is_1(self):
        chunk = make_chunk("solo")
        result = attnres_fusion([chunk])
        assert len(result) == 1
        assert result[0].rank == 1

    def test_no_embeddings_falls_back_to_score_ordering(self):
        """Without query_embedding, result is sorted by original score."""
        chunks = [
            make_chunk("low", score=0.1),
            make_chunk("high", score=0.9),
            make_chunk("mid", score=0.5),
        ]
        result = attnres_fusion(chunks, query_embedding=None)
        ids = [c.chunk_id for c in result]
        assert ids == ["high", "mid", "low"]

    def test_source_weights_boost_memory_chunks(self):
        """source_weights={"memory": 2.0} boosts memory chunks above equal-scored session chunks."""
        session_chunk = make_chunk("s1", source="session_file", score=1.0)
        memory_chunk = make_chunk("m1", source="memory", score=1.0)
        result = attnres_fusion(
            [session_chunk, memory_chunk],
            source_weights={"memory": 2.0},
        )
        ids = [c.chunk_id for c in result]
        assert ids[0] == "m1"

    def test_ranks_assigned_1_indexed(self):
        chunks = [make_chunk(f"c{i}", score=float(i)) for i in range(3)]
        result = attnres_fusion(chunks)
        ranks = [c.rank for c in result]
        assert ranks == [1, 2, 3]

    def test_query_embedding_aligns_chunk_closer_to_query_ranks_higher(self):
        """Chunk whose embedding is closer to query ranks higher than one that is orthogonal."""
        query_emb = [1.0, 0.0]
        # Two chunks with same base score but different embeddings
        # We'll put embeddings in metadata for this test since RetrievedChunk has metadata
        # The implementation should use metadata["embedding"] if available
        chunk_aligned = RetrievedChunk(
            chunk_id="aligned",
            content="aligned",
            source="memory",
            score=1.0,
            metadata={"embedding": [1.0, 0.0]},
        )
        chunk_orthogonal = RetrievedChunk(
            chunk_id="orthogonal",
            content="orthogonal",
            source="memory",
            score=1.0,
            metadata={"embedding": [0.0, 1.0]},
        )
        result = attnres_fusion([chunk_aligned, chunk_orthogonal], query_embedding=query_emb)
        ids = [c.chunk_id for c in result]
        assert ids[0] == "aligned"
