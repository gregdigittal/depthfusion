"""Tests for fusion/materialisation_policy.py and fusion/chunk_state_compression.py — S-130 AC-3.

Covers:
  1. Score threshold gate — items below threshold are rejected (deferred)
  2. Novelty gate — near-duplicate items rejected; different items accepted
  3. Capacity management — eviction of lowest-score items
  4. Decision log invariant (I-8) — one decision per input item
  5. Store management — get_store / load_store / clear
  6. ChunkStateCompressor — round-trip: initial → absorb → correct state
  7. ChunkStateCompressor — topic drift detection
  8. ChunkStateCompressor — entity deduplication and cap
"""
from __future__ import annotations

import pytest

from depthfusion.fusion.chunk_state_compression import ChunkStateCompressor
from depthfusion.fusion.materialisation_policy import MaterialisableItem, MaterialisationPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    id: str,
    score: float,
    embedding: list[float] | None = None,
) -> MaterialisableItem:
    return MaterialisableItem(
        id=id,
        score=score,
        content=f"Content for {id}",
        embedding=embedding,
        metadata={},
    )


def _unit(dim: int, idx: int) -> list[float]:
    v = [0.0] * dim
    v[idx] = 1.0
    return v


# ---------------------------------------------------------------------------
# 1. Score threshold gate (include / defer decision)
# ---------------------------------------------------------------------------

class TestScoreThresholdGate:
    def test_rejects_item_below_threshold(self):
        policy = MaterialisationPolicy(score_threshold=0.5)
        result = policy.evaluate([_item("low", 0.2)])

        assert len(result.accepted) == 0
        assert len(result.rejected) == 1
        assert result.decisions[0].passed_score_threshold is False
        assert "below threshold" in result.decisions[0].reason

    def test_accepts_item_at_threshold(self):
        policy = MaterialisationPolicy(score_threshold=0.5)
        result = policy.evaluate([_item("exact", 0.5)])
        assert len(result.accepted) == 1

    def test_accepts_item_above_threshold(self):
        policy = MaterialisationPolicy(score_threshold=0.5)
        result = policy.evaluate([_item("high", 0.9)])
        assert len(result.accepted) == 1

    def test_mixed_batch_score_gate(self):
        policy = MaterialisationPolicy(score_threshold=0.5)
        result = policy.evaluate([_item("bad", 0.1), _item("good", 0.8)])
        assert len(result.accepted) == 1
        assert len(result.rejected) == 1
        assert result.accepted[0].id == "good"


# ---------------------------------------------------------------------------
# 2. Novelty gate (reference decision — too similar)
# ---------------------------------------------------------------------------

class TestNoveltyGate:
    def test_first_item_is_maximally_novel(self):
        policy = MaterialisationPolicy()
        result = policy.evaluate([_item("first", 0.8, _unit(3, 0))])
        assert len(result.accepted) == 1
        assert result.decisions[0].novelty_score == pytest.approx(1.0)

    def test_near_duplicate_rejected(self):
        policy = MaterialisationPolicy(novelty_threshold=0.3)
        policy.evaluate([_item("a", 0.8, [1.0, 0.0, 0.0])])
        # Almost identical embedding
        result = policy.evaluate([_item("b", 0.8, [0.999, 0.001, 0.0])])
        assert len(result.rejected) == 1
        assert result.decisions[0].passed_novelty_gate is False
        assert "too similar" in result.decisions[0].reason

    def test_orthogonal_embedding_accepted(self):
        policy = MaterialisationPolicy(novelty_threshold=0.2)
        policy.evaluate([_item("a", 0.8, _unit(3, 0))])
        result = policy.evaluate([_item("b", 0.8, _unit(3, 1))])  # orthogonal
        assert len(result.accepted) == 1
        assert result.decisions[0].novelty_score > 0.5

    def test_id_dedup_fallback_same_id_rejected(self):
        policy = MaterialisationPolicy()
        policy.evaluate([_item("dup", 0.8)])
        result = policy.evaluate([_item("dup", 0.9)])
        assert len(result.rejected) == 1

    def test_id_dedup_fallback_different_id_accepted(self):
        policy = MaterialisationPolicy()
        policy.evaluate([_item("a", 0.8)])
        result = policy.evaluate([_item("b", 0.8)])
        assert len(result.accepted) == 1


# ---------------------------------------------------------------------------
# 3. Capacity management (eviction)
# ---------------------------------------------------------------------------

class TestCapacityManagement:
    def test_evicts_lowest_score_when_over_capacity(self):
        policy = MaterialisationPolicy(max_capacity=3)
        policy.evaluate([_item("a", 0.5), _item("b", 0.7), _item("c", 0.9)])
        assert policy.size == 3

        result = policy.evaluate([_item("d", 0.8), _item("e", 0.6)])
        assert policy.size == 3
        assert len(result.evicted) == 2
        evicted_ids = {ev.id for ev in result.evicted}
        assert "a" in evicted_ids  # score 0.5 — lowest
        assert "e" in evicted_ids  # score 0.6 — second lowest

    def test_no_eviction_under_capacity(self):
        policy = MaterialisationPolicy(max_capacity=10)
        result = policy.evaluate([_item("a", 0.5), _item("b", 0.7)])
        assert len(result.evicted) == 0

    def test_store_size_never_exceeds_capacity(self):
        policy = MaterialisationPolicy(max_capacity=2)
        for i in range(10):
            policy.evaluate([_item(f"item-{i}", 0.5 + i * 0.01)])
        assert policy.size <= 2


# ---------------------------------------------------------------------------
# 4. Decision log invariant (I-8)
# ---------------------------------------------------------------------------

class TestDecisionLog:
    def test_one_decision_per_input_item(self):
        policy = MaterialisationPolicy()
        items = [_item("a", 0.5), _item("b", 0.01), _item("c", 0.8)]
        result = policy.evaluate(items)
        assert len(result.decisions) == 3
        assert [d.item_id for d in result.decisions] == ["a", "b", "c"]

    def test_every_decision_has_non_empty_reason(self):
        policy = MaterialisationPolicy(score_threshold=0.5)
        result = policy.evaluate([_item("x", 0.1), _item("y", 0.9)])
        for dec in result.decisions:
            assert dec.reason


# ---------------------------------------------------------------------------
# 5. Store management
# ---------------------------------------------------------------------------

class TestStoreManagement:
    def test_get_store_returns_accepted_items(self):
        policy = MaterialisationPolicy()
        policy.evaluate([_item("a", 0.8), _item("b", 0.9)])
        assert len(policy.get_store()) == 2

    def test_load_store_replaces_contents(self):
        policy = MaterialisationPolicy()
        policy.evaluate([_item("a", 0.8)])
        policy.load_store([_item("x", 0.5), _item("y", 0.6), _item("z", 0.7)])
        assert policy.size == 3

    def test_clear_empties_store(self):
        policy = MaterialisationPolicy()
        policy.evaluate([_item("a", 0.8)])
        policy.clear()
        assert policy.size == 0


# ---------------------------------------------------------------------------
# 6. ChunkStateCompressor — round-trip
# ---------------------------------------------------------------------------

class TestChunkStateCompressor:
    def test_initial_state_has_zero_vector_and_empty_entities(self):
        comp = ChunkStateCompressor(vector_dim=4)
        state = comp.initial()
        assert state.chunk_index == 0
        assert len(state.topic_vector) == 4
        assert all(v == 0.0 for v in state.topic_vector)
        assert state.key_entities == ()

    def test_absorb_increments_chunk_index(self):
        comp = ChunkStateCompressor(vector_dim=4)
        state = comp.initial()
        state = comp.absorb(state, [1.0, 0.0, 0.0, 0.0], ["entity-a"], 0.8)
        assert state.chunk_index == 1

    def test_absorb_round_trip_score_stats(self):
        comp = ChunkStateCompressor(vector_dim=4)
        state = comp.initial()
        state = comp.absorb(state, None, [], 0.7)
        state = comp.absorb(state, None, [], 0.3)
        assert state.score_stats.count == 2
        assert state.score_stats.min == pytest.approx(0.3)
        assert state.score_stats.max == pytest.approx(0.7)
        assert state.score_stats.mean == pytest.approx(0.5)

    def test_decay_factor_decreases_with_chunks(self):
        comp = ChunkStateCompressor(vector_dim=2, base_decay=0.9, min_decay=0.1)
        state = comp.initial()
        prev_decay = state.decay_factor  # 1.0
        for _ in range(5):
            state = comp.absorb(state, None, [], 0.5)
            assert state.decay_factor <= prev_decay
            prev_decay = state.decay_factor

    def test_decay_floored_at_min_decay(self):
        comp = ChunkStateCompressor(vector_dim=2, base_decay=0.5, min_decay=0.15)
        state = comp.initial()
        for _ in range(20):
            state = comp.absorb(state, None, [], 0.5)
        assert state.decay_factor >= 0.15 - 1e-9

    def test_topic_vector_moves_toward_embedding(self):
        comp = ChunkStateCompressor(vector_dim=3)
        state = comp.initial()
        state = comp.absorb(state, [1.0, 0.0, 0.0], [], 0.5)
        assert state.topic_vector[0] > 0.0
        assert state.topic_vector[1] == pytest.approx(0.0)

    def test_mean_score_zero_on_initial_state(self):
        comp = ChunkStateCompressor()
        state = comp.initial()
        assert comp.mean_score(state) == 0.0


# ---------------------------------------------------------------------------
# 7. Topic drift detection
# ---------------------------------------------------------------------------

class TestTopicDrift:
    def test_no_drift_on_initial_state(self):
        comp = ChunkStateCompressor(vector_dim=3)
        state = comp.initial()
        assert comp.has_topic_drift(state, [1.0, 0.0, 0.0]) is False

    def test_drift_detected_on_orthogonal_embedding(self):
        comp = ChunkStateCompressor(vector_dim=3)
        state = comp.initial()
        state = comp.absorb(state, [1.0, 0.0, 0.0], [], 0.8)
        # Orthogonal to accumulated topic → cosine ≈ 0 < threshold 0.3
        assert comp.has_topic_drift(state, [0.0, 1.0, 0.0]) is True

    def test_no_drift_on_aligned_embedding(self):
        comp = ChunkStateCompressor(vector_dim=3)
        state = comp.initial()
        state = comp.absorb(state, [1.0, 0.0, 0.0], [], 0.8)
        # Same direction — cosine ≈ 1.0 > threshold 0.3
        assert comp.has_topic_drift(state, [0.9, 0.1, 0.0]) is False


# ---------------------------------------------------------------------------
# 8. Entity deduplication and cap
# ---------------------------------------------------------------------------

class TestEntityHandling:
    def test_entities_deduplicated_case_insensitive(self):
        comp = ChunkStateCompressor(vector_dim=2)
        state = comp.initial()
        state = comp.absorb(state, None, ["Python", "python", "PYTHON"], 0.5)
        assert len(state.key_entities) == 1

    def test_entities_capped_at_max_entities(self):
        comp = ChunkStateCompressor(vector_dim=2, max_entities=3)
        state = comp.initial()
        state = comp.absorb(state, None, ["a", "b", "c", "d", "e"], 0.5)
        assert len(state.key_entities) <= 3

    def test_new_entities_prepended_over_old(self):
        comp = ChunkStateCompressor(vector_dim=2, max_entities=4)
        state = comp.initial()
        state = comp.absorb(state, None, ["old1", "old2"], 0.5)
        state = comp.absorb(state, None, ["new1", "new2"], 0.5)
        # new entities should appear before old
        assert state.key_entities[0] == "new1"
        assert state.key_entities[1] == "new2"
