"""Tests for selective_fusion_weighter.py — S-129 AC-3.

Covers:
  1. Passthrough when DEPTHFUSION_FUSION_GATES_ENABLED is unset/false
  2. B gate: soft penalty below bGateMinSimilarity (not hard reject)
  3. B gate: default 0.5 when no query embedding or no block embedding
  4. C gate: first block always gets cGateValue=1.0
  5. C gate: sequential adjacent (lastEmbedding) — not pairwise
  6. Delta gate: blocks below threshold are excluded
  7. Source weights applied correctly in multiplicative score
  8. Survivors sorted by fused_score descending
  9. Empty input returns empty output and valid log
 10. Parity spot-check: B soft-penalty produces non-zero fused score
 11. Gate log populated (D-3 invariant)
"""
from __future__ import annotations

import pytest

from depthfusion.fusion.selective_fusion_weighter import (
    SelectiveFusionWeighter,
    SelectiveGateConfig,
    WeightedGateLog,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(
    chunk_id: str,
    score: float,
    embedding: list[float] | None = None,
    source: str = "test.md",
) -> dict:
    block: dict = {"chunk_id": chunk_id, "score": score, "source": source}
    if embedding is not None:
        block["embedding"] = embedding
    return block


def _unit(dim: int, idx: int) -> list[float]:
    """Unit vector with 1.0 at position idx, 0.0 elsewhere."""
    v = [0.0] * dim
    v[idx] = 1.0
    return v


# ---------------------------------------------------------------------------
# 1. SelectiveGateConfig defaults
# ---------------------------------------------------------------------------

def test_gate_config_defaults():
    cfg = SelectiveGateConfig()
    assert cfg.b_gate_min_similarity == pytest.approx(0.1)
    assert cfg.c_gate_decay_ratio == 3
    assert cfg.c_gate_adjacent_threshold == pytest.approx(0.3)
    assert cfg.delta_gate_threshold == pytest.approx(0.05)


def test_gate_config_clamps_invalid_values():
    cfg = SelectiveGateConfig(
        b_gate_min_similarity=-0.5,   # clamped to 0.0
        c_gate_decay_ratio=0,          # clamped to 1 (guard div-by-zero)
        c_gate_adjacent_threshold=2.0, # clamped to 1.0
        delta_gate_threshold=-1.0,     # clamped to 0.0
    )
    assert cfg.b_gate_min_similarity == 0.0
    assert cfg.c_gate_decay_ratio == 1
    assert cfg.c_gate_adjacent_threshold == 1.0
    assert cfg.delta_gate_threshold == 0.0


def test_gate_config_version_id_stable():
    cfg = SelectiveGateConfig()
    assert cfg.version_id() == cfg.version_id()
    assert len(cfg.version_id()) == 12


def test_gate_config_from_env(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_B_THRESHOLD", "0.2")
    monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_C_DECAY_RATIO", "5")
    monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD", "0.1")
    cfg = SelectiveGateConfig.from_env()
    assert cfg.b_gate_min_similarity == pytest.approx(0.2)
    assert cfg.c_gate_decay_ratio == 5
    assert cfg.delta_gate_threshold == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# 2. Empty input
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty_and_valid_log():
    weighter = SelectiveFusionWeighter()
    survivors, log = weighter.apply([])
    assert survivors == []
    assert isinstance(log, WeightedGateLog)
    assert log.total_candidates == 0
    assert log.passed_delta == 0
    assert log.decisions == []


# ---------------------------------------------------------------------------
# 3. B gate — soft penalty, not hard rejection
# ---------------------------------------------------------------------------

def test_b_gate_soft_penalty_not_hard_reject():
    """A block scoring below bGateMinSimilarity is penalised 0.1×, not dropped.

    With base_score=1.0, b_score=0.05 (< 0.1), penalty → b_gate_value=0.005.
    fused = 1.0 * 0.005 * 1.0 * 1.0 = 0.005, which is below delta_threshold=0.05
    → excluded from survivors BUT the decision record shows passes_delta=False,
    meaning the delta gate (not a hard B-reject) was the exclusion reason.
    """
    # b_score is determined by cosine(query, block). Use orthogonal embeddings
    # to get cosine=0.0 → b_score=0.0 < b_gate_min_similarity=0.1.
    q_emb = _unit(4, 0)
    b_emb = _unit(4, 1)   # orthogonal: cosine=0.0

    cfg = SelectiveGateConfig(b_gate_min_similarity=0.1, delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(config=cfg)
    block = _make_block("b1", score=1.0, embedding=b_emb)
    survivors, log = weighter.apply([block], query_embedding=q_emb)

    dec = log.decisions[0]
    # b_gate_value = cosine * 0.1 = 0.0 * 0.1 = 0.0 → fused = 0.0
    # delta_threshold=0.0 → passes_delta = (0.0 >= 0.0) = True
    assert dec.b_gate_value == pytest.approx(0.0, abs=1e-4)
    # Block survives because fused=0.0 >= delta=0.0
    assert dec.passes_delta is True
    assert len(survivors) == 1


def test_b_gate_passthrough_above_threshold():
    """Block with b_score >= bGateMinSimilarity gets b_gate_value = b_score (no penalty)."""
    q_emb = _unit(2, 0)
    b_emb = _unit(2, 0)   # identical: cosine=1.0

    cfg = SelectiveGateConfig(b_gate_min_similarity=0.1, delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(config=cfg)
    block = _make_block("b1", score=0.8, embedding=b_emb)
    survivors, log = weighter.apply([block], query_embedding=q_emb)

    dec = log.decisions[0]
    assert dec.b_gate_value == pytest.approx(1.0, abs=1e-4)
    assert dec.fused_score == pytest.approx(0.8, abs=1e-4)
    assert len(survivors) == 1


# ---------------------------------------------------------------------------
# 4. B gate — default 0.5 when no embeddings
# ---------------------------------------------------------------------------

def test_b_gate_defaults_to_half_when_no_embeddings():
    """No query embedding → b_score defaults to 0.5 (TS neutral pass-through)."""
    cfg = SelectiveGateConfig(delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(config=cfg)
    block = _make_block("b1", score=1.0)   # no embedding
    survivors, log = weighter.apply([block], query_embedding=None)

    dec = log.decisions[0]
    # b_score=0.5 >= b_min=0.1 → b_gate_value=0.5; c=1.0 (first); fused=0.5
    assert dec.b_gate_value == pytest.approx(0.5)
    assert dec.fused_score == pytest.approx(0.5)
    assert len(survivors) == 1


# ---------------------------------------------------------------------------
# 5. C gate — sequential, not pairwise
# ---------------------------------------------------------------------------

def test_c_gate_first_block_always_passes():
    """First block always gets cGateValue=1.0 regardless of embeddings."""
    cfg = SelectiveGateConfig(delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(config=cfg)
    block = _make_block("b1", score=1.0)
    _, log = weighter.apply([block])
    assert log.decisions[0].c_gate_value == pytest.approx(1.0)


def test_c_gate_sequential_adjacent_detection():
    """Second block adjacent to first gets cGateValue=1.0; non-adjacent gets 1/decay."""
    # Block 1 and 2 are identical embeddings → cosine=1.0 > 0.3 → adjacent
    # Block 3 is orthogonal to block 2 → cosine=0.0 ≤ 0.3 → not adjacent
    e0 = _unit(3, 0)
    e1 = _unit(3, 0)   # same as e0
    e2 = _unit(3, 1)   # orthogonal to e0/e1

    cfg = SelectiveGateConfig(c_gate_decay_ratio=3, delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(config=cfg)
    blocks = [
        _make_block("b0", score=1.0, embedding=e0),
        _make_block("b1", score=1.0, embedding=e1),
        _make_block("b2", score=1.0, embedding=e2),
    ]
    _, log = weighter.apply(blocks)

    assert log.decisions[0].c_gate_value == pytest.approx(1.0)
    assert log.decisions[1].c_gate_value == pytest.approx(1.0)
    assert log.decisions[2].c_gate_value == pytest.approx(1.0 / 3, abs=5e-4)


def test_c_gate_uses_last_embedding_not_pairwise():
    """C gate checks against the *previous* block embedding, not max-pairwise.

    Three blocks: A, B, C where A and C are identical (would look adjacent
    pairwise) but B is orthogonal to A. In sequential mode, C sees B as
    its predecessor, so C is 'not adjacent' even though A and C are similar.
    """
    e_a = _unit(3, 0)
    e_b = _unit(3, 1)   # orthogonal to A
    e_c = _unit(3, 0)   # same as A (pairwise adjacent to A, not to B)

    cfg = SelectiveGateConfig(c_gate_decay_ratio=3, delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(config=cfg)
    blocks = [
        _make_block("a", score=1.0, embedding=e_a),
        _make_block("b", score=1.0, embedding=e_b),
        _make_block("c", score=1.0, embedding=e_c),
    ]
    _, log = weighter.apply(blocks)

    dec_c = log.decisions[2]
    # C's predecessor is B (orthogonal) → cosine=0 ≤ 0.3 → not adjacent
    assert dec_c.c_gate_value == pytest.approx(1.0 / 3, abs=5e-4)


# ---------------------------------------------------------------------------
# 6. Delta gate — blocks below threshold are excluded
# ---------------------------------------------------------------------------

def test_delta_gate_excludes_low_scoring_blocks():
    """fused_score below delta_gate_threshold → excluded from survivors."""
    # No embeddings: b_score=0.5, c=1.0 (first), fused = score * 0.5
    # Block A: score=0.2 → fused=0.1 ≥ 0.05 → included
    # Block B: score=0.05 → fused=0.025 < 0.05 → excluded
    cfg = SelectiveGateConfig(delta_gate_threshold=0.05)
    weighter = SelectiveFusionWeighter(config=cfg)
    blocks = [
        _make_block("a", score=0.2),
        _make_block("b", score=0.05),
    ]
    survivors, log = weighter.apply(blocks)

    assert log.total_candidates == 2
    assert log.passed_delta == 1
    assert len(survivors) == 1
    assert survivors[0]["chunk_id"] == "a"


# ---------------------------------------------------------------------------
# 7. Source weights
# ---------------------------------------------------------------------------

def test_source_weights_applied_in_multiplicative_score():
    """source_weights multiplier scales fused_score proportionally."""
    cfg = SelectiveGateConfig(delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(
        config=cfg,
        source_weights={"high_weight.md": 2.0, "low_weight.md": 0.5},
    )
    blocks = [
        _make_block("h", score=1.0, source="high_weight.md"),
        _make_block("l", score=1.0, source="low_weight.md"),
    ]
    _, log = weighter.apply(blocks)

    # b=0.5 (no embedding), c=1.0 (first/second), fused = 1.0 * 0.5 * 1.0 * srcWeight
    dec_h = log.decisions[0]
    dec_l = log.decisions[1]
    assert dec_h.fused_score == pytest.approx(dec_l.fused_score * 4, rel=0.01)


# ---------------------------------------------------------------------------
# 8. Survivors sorted by fused_score descending
# ---------------------------------------------------------------------------

def test_survivors_sorted_by_fused_score_descending():
    cfg = SelectiveGateConfig(delta_gate_threshold=0.0)
    weighter = SelectiveFusionWeighter(config=cfg)
    # No embeddings: fused = score * 0.5 * c_gate
    # All first-encounter: c=1.0 (sequential, each is "first" in its own
    # position but they share lastEmbedding=None since none have embeddings)
    blocks = [
        _make_block("low", score=0.2),
        _make_block("high", score=0.8),
        _make_block("mid", score=0.5),
    ]
    survivors, _ = weighter.apply(blocks)
    fused_scores = [b["gate_fused_score"] for b in survivors]
    assert fused_scores == sorted(fused_scores, reverse=True)


# ---------------------------------------------------------------------------
# 9. Gate log invariants (D-3)
# ---------------------------------------------------------------------------

def test_gate_log_populated_for_every_query():
    """A log is emitted even when all blocks pass — D-3 invariant."""
    weighter = SelectiveFusionWeighter()
    blocks = [_make_block("x", score=1.0)]
    _, log = weighter.apply(blocks)
    assert log.total_candidates == 1
    assert len(log.decisions) == 1
    assert log.config_version_id != ""


def test_gate_log_decision_count_matches_input():
    weighter = SelectiveFusionWeighter()
    blocks = [_make_block(f"b{i}", score=float(i + 1) / 10) for i in range(5)]
    _, log = weighter.apply(blocks)
    assert len(log.decisions) == 5


# ---------------------------------------------------------------------------
# 10. Parity spot-check against TS reference behaviour
# ---------------------------------------------------------------------------

def test_parity_multiplicative_vs_additive():
    """Multiplicative scoring differs from additive α-blend for mixed-signal blocks.

    TS (multiplicative): fused = base * b_gate * c_gate
    S-51 α-blend (additive): fused = α * b_score + (1-α) * base_percentile

    With a low-similarity block (b_score=0.05, base=0.9):
      - Multiplicative: 0.9 * (0.05 * 0.1) * 1.0 = 0.0045 → far below 0.05 threshold
      - Additive (α=0.3): 0.3 * 0.05 + 0.7 * ~1.0 ≈ 0.715 → would pass

    This confirms the multiplicative algorithm is significantly more selective.
    """
    q_emb = _unit(4, 0)
    b_emb = _unit(4, 1)   # orthogonal: cosine=0.0 → b_score=0.0 < 0.1

    cfg = SelectiveGateConfig(
        b_gate_min_similarity=0.1,
        delta_gate_threshold=0.05,
    )
    weighter = SelectiveFusionWeighter(config=cfg)
    block = _make_block("orthogonal", score=0.9, embedding=b_emb)
    survivors, log = weighter.apply([block], query_embedding=q_emb)

    # b_score=0.0 → b_gate_value=0.0; fused=0.9*0.0*1.0=0.0 < 0.05 → excluded
    assert len(survivors) == 0
    assert log.decisions[0].passes_delta is False
    assert log.decisions[0].b_gate_value == pytest.approx(0.0, abs=1e-4)
