"""ChunkStateCompression — Fixed-size boundary state between chunks.

Mamba SSMs carry a fixed-size hidden state h_t across timesteps.
This module applies the same principle to multi-step fusion:

  When processing a document in chunks (e.g. partition_map strategy),
  each chunk boundary emits a compressed state that captures:
    - Topic vector (running centroid of chunk embeddings)
    - Key entity set (bounded, LRU-evicted)
    - Running score statistics (min, max, mean, count)
    - Decay factor (how much prior context should influence the next chunk)

  The next chunk consumes this state to maintain cross-chunk coherence
  without re-reading prior chunks.

Port of chunk-state-compression.ts (SkillForge depthfusion-core).
Zero DepthFusion internal imports — standalone fusion primitive.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_DEFAULT_MAX_ENTITIES = 50
_DEFAULT_VECTOR_DIM = 64
_DEFAULT_BASE_DECAY = 0.95
_DEFAULT_MIN_DECAY = 0.1


@dataclass(frozen=True)
class ScoreStats:
    min: float
    max: float
    sum: float
    count: int

    @property
    def mean(self) -> float:
        return self.sum / self.count if self.count > 0 else 0.0


@dataclass(frozen=True)
class ChunkBoundaryState:
    """Immutable compressed state passed between chunk boundaries."""

    topic_vector: tuple[float, ...]
    key_entities: tuple[str, ...]
    score_stats: ScoreStats
    decay_factor: float
    chunk_index: int


class ChunkStateCompressor:
    """Maintains cross-chunk coherence via a fixed-size hidden state.

    Usage::

        compressor = ChunkStateCompressor()
        state = compressor.initial()
        for chunk in chunks:
            state = compressor.absorb(state, chunk.embedding, chunk.entities, chunk.score)
    """

    def __init__(
        self,
        *,
        max_entities: int = _DEFAULT_MAX_ENTITIES,
        vector_dim: int = _DEFAULT_VECTOR_DIM,
        base_decay: float = _DEFAULT_BASE_DECAY,
        min_decay: float = _DEFAULT_MIN_DECAY,
    ) -> None:
        self._max_entities = max_entities
        self._vector_dim = vector_dim
        self._base_decay = base_decay
        self._min_decay = min_decay

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initial(self) -> ChunkBoundaryState:
        """Create an empty initial state (before any chunks are processed)."""
        return ChunkBoundaryState(
            topic_vector=tuple(0.0 for _ in range(self._vector_dim)),
            key_entities=(),
            score_stats=ScoreStats(min=math.inf, max=-math.inf, sum=0.0, count=0),
            decay_factor=1.0,
            chunk_index=0,
        )

    def absorb(
        self,
        prev: ChunkBoundaryState,
        chunk_embedding: list[float] | None,
        chunk_entities: list[str],
        chunk_score: float,
    ) -> ChunkBoundaryState:
        """Absorb a chunk into the running state, producing the next boundary state.

        Args:
            prev: State from the previous chunk boundary (or initial()).
            chunk_embedding: Embedding vector for this chunk (or None if unavailable).
            chunk_entities: Entities extracted from this chunk.
            chunk_score: Relevance score for this chunk.

        Returns:
            Updated boundary state for the next chunk.
        """
        next_index = prev.chunk_index + 1

        # Topic vector: exponential moving average of embeddings
        if chunk_embedding and len(chunk_embedding) == self._vector_dim:
            alpha = 1.0 / (next_index + 1)
            topic_vector = tuple(
                (1.0 - alpha) * v + alpha * chunk_embedding[i]
                for i, v in enumerate(prev.topic_vector)
            )
        else:
            topic_vector = prev.topic_vector

        # Key entities: prepend new, deduplicate, cap at max_entities
        seen: set[str] = set()
        merged: list[str] = []
        for e in chunk_entities:
            norm = e.strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                merged.append(e.strip())
        for e in prev.key_entities:
            norm = e.strip().lower()
            if norm not in seen:
                seen.add(norm)
                merged.append(e)
        key_entities = tuple(merged[: self._max_entities])

        # Score statistics
        prev_min = prev.score_stats.min
        prev_max = prev.score_stats.max
        score_stats = ScoreStats(
            min=chunk_score if math.isinf(prev_min) else min(prev_min, chunk_score),
            max=chunk_score if math.isinf(prev_max) else max(prev_max, chunk_score),
            sum=prev.score_stats.sum + chunk_score,
            count=prev.score_stats.count + 1,
        )

        # Decay: base_decay^chunk_index, floored at min_decay
        decay_factor = max(self._min_decay, self._base_decay**next_index)

        return ChunkBoundaryState(
            topic_vector=topic_vector,
            key_entities=key_entities,
            score_stats=score_stats,
            decay_factor=decay_factor,
            chunk_index=next_index,
        )

    def has_topic_drift(
        self,
        state: ChunkBoundaryState,
        new_embedding: list[float],
        threshold: float = 0.3,
    ) -> bool:
        """True when new_embedding has drifted from the accumulated topic vector.

        Useful for detecting when a document changes topic mid-stream.
        Returns False when no prior state exists or dimensions mismatch.
        """
        if state.chunk_index == 0:
            return False
        if len(new_embedding) != self._vector_dim:
            return False
        sim = _cosine(list(state.topic_vector), new_embedding)
        return sim < threshold

    def state_byte_estimate(self, state: ChunkBoundaryState) -> int:
        """Fixed-size byte estimate for the state (for budget calculations)."""
        vector_bytes = len(state.topic_vector) * 8
        entity_bytes = sum(len(e) * 2 for e in state.key_entities)
        return vector_bytes + entity_bytes + 64

    def mean_score(self, state: ChunkBoundaryState) -> float:
        return state.score_stats.mean


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(ai * bi for ai, bi in zip(a, b))
    norm_a = math.sqrt(sum(ai * ai for ai in a))
    norm_b = math.sqrt(sum(bi * bi for bi in b))
    denom = norm_a * norm_b
    return dot / denom if denom != 0.0 else 0.0
