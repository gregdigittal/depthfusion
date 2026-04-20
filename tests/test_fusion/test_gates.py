# tests/test_fusion/test_gates.py
"""Selective fusion gates tests — S-51 / T-159.

AC-4: ≥ 12 new tests. AC-3: parity with the TS reference implementation
on 20 deterministic test cases. The parity matrix encodes the contract
the Python port MUST satisfy — hand-crafted with known inputs → expected
verdicts so the Python implementation can be validated against the TS
reference wire-for-wire without needing to run a TS runtime.
"""
from __future__ import annotations

import pytest

from depthfusion.fusion.gates import (
    GateConfig,
    GateDecision,
    GateLog,
    SelectiveFusionGates,
    _bm25_percentile,
    _cosine,
)

# ---------------------------------------------------------------------------
# GateConfig
# ---------------------------------------------------------------------------

class TestGateConfig:
    def test_defaults_match_build_plan(self):
        cfg = GateConfig()
        # DEPTHFUSION_FUSION_GATES_ALPHA default = 0.30 per TG-11
        assert cfg.alpha == 0.30
        assert 0.0 <= cfg.b_threshold <= 1.0
        assert 0.0 <= cfg.c_threshold <= 1.0
        assert cfg.delta_threshold >= 0.0

    def test_alpha_clamped_to_unit_interval(self):
        assert GateConfig(alpha=-5.0).alpha == 0.0
        assert GateConfig(alpha=99.0).alpha == 1.0

    def test_thresholds_clamped(self):
        cfg = GateConfig(b_threshold=2.0, c_threshold=-1.0)
        assert cfg.b_threshold == 1.0
        assert cfg.c_threshold == 0.0

    def test_from_env_reads_alpha(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ALPHA", "0.75")
        cfg = GateConfig.from_env()
        assert cfg.alpha == 0.75

    def test_from_env_ignores_malformed(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ALPHA", "not-a-number")
        cfg = GateConfig.from_env()
        assert cfg.alpha == 0.30  # fell back to default

    def test_config_is_frozen(self):
        from dataclasses import FrozenInstanceError
        cfg = GateConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.alpha = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestCosineHelper:
    def test_identical_is_one(self):
        assert _cosine([1, 0, 0], [1, 0, 0]) == 1.0

    def test_orthogonal_is_zero(self):
        assert _cosine([1, 0], [0, 1]) == 0.0

    def test_degenerate_inputs_zero_not_raise(self):
        assert _cosine(None, [1]) == 0.0
        assert _cosine([], [1]) == 0.0
        assert _cosine([1, 2], [1, 2, 3]) == 0.0
        assert _cosine([0, 0], [1, 1]) == 0.0


class TestBm25Percentile:
    def test_top_score_percentile_is_one(self):
        assert _bm25_percentile(10.0, [1.0, 5.0, 10.0]) == 1.0

    def test_bottom_score_percentile_is_zero(self):
        assert _bm25_percentile(1.0, [1.0, 5.0, 10.0]) == 0.0

    def test_single_score_with_positive_value(self):
        assert _bm25_percentile(5.0, [5.0]) == 1.0

    def test_empty_corpus_returns_zero(self):
        assert _bm25_percentile(5.0, []) == 0.0

    def test_ties_share_percentile(self):
        """Tied scores → equal percentile (stable, non-arbitrary)."""
        p1 = _bm25_percentile(5.0, [5.0, 5.0, 10.0])
        p2 = _bm25_percentile(5.0, [5.0, 5.0, 10.0])
        assert p1 == p2


# ---------------------------------------------------------------------------
# SelectiveFusionGates — core behaviour
# ---------------------------------------------------------------------------

def _block(chunk_id: str, score: float, embedding: list[float] | None = None) -> dict:
    b: dict = {"chunk_id": chunk_id, "score": score}
    if embedding is not None:
        b["embedding"] = embedding
    return b


class TestGateBehaviour:
    def test_empty_blocks_returns_empty(self):
        gates = SelectiveFusionGates()
        survivors, log = gates.apply([])
        assert survivors == []
        assert log.total_candidates == 0
        assert log.passed_delta == 0

    def test_single_block_c_gate_exempt(self):
        """With n=1 the C gate's 'adjacent similarity' is undefined; exempt."""
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.0, c_threshold=0.99, delta_threshold=0.0),
        )
        survivors, log = gates.apply([_block("only", score=1.0)])
        assert len(survivors) == 1
        assert log.decisions[0].passes_c is True

    def test_b_gate_rejects_low_similarity(self):
        """Block with BM25 percentile below b_threshold fails B."""
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.9, c_threshold=0.0, delta_threshold=0.0),
        )
        blocks = [
            _block("high", score=10.0),
            _block("low", score=0.1),
        ]
        _, log = gates.apply(blocks)
        # "low" has percentile 0.0 → fails b_threshold=0.9
        low_dec = next(d for d in log.decisions if d.chunk_id == "low")
        assert low_dec.passes_b is False

    def test_embedding_b_gate_uses_cosine(self):
        """When query_embedding is present, B gate uses cosine, not BM25 percentile."""
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.5, c_threshold=0.0, delta_threshold=0.0),
        )
        blocks = [
            _block("aligned", score=1.0, embedding=[1.0, 0.0, 0.0]),
            _block("orthog", score=1.0, embedding=[0.0, 1.0, 0.0]),
        ]
        _, log = gates.apply(blocks, query_embedding=[1.0, 0.0, 0.0])
        decs = {d.chunk_id: d for d in log.decisions}
        assert decs["aligned"].passes_b is True   # cos=1.0 ≥ 0.5
        assert decs["orthog"].passes_b is False   # cos=0.0 < 0.5

    def test_delta_gate_filters_low_fused_scores(self):
        """Chunks with fused_score below delta_threshold fail Δ.

        Post-review (HIGH-1 fix): `base_score` is normalised to percentile
        ∈ [0,1] before the α blend, so `fused_score` is also ∈ [0,1].
        Thresholds are expressed on that normalised scale.
        """
        gates = SelectiveFusionGates(
            GateConfig(
                alpha=0.5, b_threshold=0.0, c_threshold=0.0, delta_threshold=0.5,
            ),
        )
        blocks = [
            _block("big", score=20.0),
            _block("small", score=1.0),
        ]
        survivors, log = gates.apply(blocks)
        survivor_ids = [b["chunk_id"] for b in survivors]
        assert "big" in survivor_ids
        assert "small" not in survivor_ids

    def test_survivors_sorted_by_fused_score_desc(self):
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
        )
        blocks = [
            _block("a", score=5.0),
            _block("b", score=20.0),
            _block("c", score=10.0),
        ]
        survivors, _ = gates.apply(blocks)
        assert [s["chunk_id"] for s in survivors] == ["b", "c", "a"]

    def test_gate_log_emitted_even_when_nothing_rejected(self):
        """D-3 invariant: log is emitted regardless of pass/fail distribution."""
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
        )
        blocks = [_block("a", score=1.0), _block("b", score=2.0)]
        _, log = gates.apply(blocks)
        assert log.total_candidates == 2
        assert log.passed_delta == 2
        # One decision record per candidate, pass or fail
        assert len(log.decisions) == 2

    def test_gate_log_records_all_four_thresholds(self):
        """Config snapshot in the log for audit reconstruction (I-8 scope)."""
        gates = SelectiveFusionGates(
            GateConfig(
                alpha=0.42, b_threshold=0.11, c_threshold=0.22, delta_threshold=1.5,
            ),
        )
        _, log = gates.apply([_block("x", score=10.0)])
        assert log.alpha == 0.42
        assert log.b_threshold == 0.11
        assert log.c_threshold == 0.22
        assert log.delta_threshold == 1.5

    def test_surviving_blocks_have_gate_scores_attached(self):
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
        )
        blocks = [_block("a", score=5.0)]
        survivors, _ = gates.apply(blocks)
        assert "gate_b_score" in survivors[0]
        assert "gate_c_score" in survivors[0]
        assert "gate_fused_score" in survivors[0]
        # Original fields preserved
        assert survivors[0]["score"] == 5.0
        assert survivors[0]["chunk_id"] == "a"

    def test_c_gate_filters_topical_orphans_with_embeddings(self):
        """With embeddings, C gate requires another block within c_threshold."""
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.0, c_threshold=0.5, delta_threshold=0.0),
        )
        # "orphan" is orthogonal to both siblings — C must reject it.
        # "sib_a" and "sib_b" are near-identical — each finds the other.
        blocks = [
            _block("sib_a", score=1.0, embedding=[1.0, 0.0, 0.0]),
            _block("sib_b", score=1.0, embedding=[0.99, 0.01, 0.0]),
            _block("orphan", score=1.0, embedding=[0.0, 0.0, 1.0]),
        ]
        _, log = gates.apply(blocks, query_embedding=[1.0, 1.0, 1.0])
        decs = {d.chunk_id: d for d in log.decisions}
        assert decs["sib_a"].passes_c is True
        assert decs["sib_b"].passes_c is True
        assert decs["orphan"].passes_c is False


# ---------------------------------------------------------------------------
# Pipeline integration (T-157)
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_apply_fusion_gates_disabled_by_default(self, monkeypatch):
        """Env var unset → apply_fusion_gates is a pass-through."""
        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        monkeypatch.delenv("DEPTHFUSION_FUSION_GATES_ENABLED", raising=False)

        p = RecallPipeline(mode=PipelineMode.LOCAL)
        blocks = [_block("a", score=1.0), _block("b", score=2.0)]
        result = p.apply_fusion_gates(blocks)
        assert result is blocks  # pass-through, not a copy

    def test_apply_fusion_gates_enabled_runs_gates(self, monkeypatch, tmp_path):
        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        # Redirect metrics to tmp so we don't pollute the real dir
        from depthfusion.metrics import collector as collector_mod
        monkeypatch.setattr(
            collector_mod, "Path", collector_mod.Path,  # keep reference
        )
        monkeypatch.setenv("HOME", str(tmp_path))
        from pathlib import Path
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        p = RecallPipeline(mode=PipelineMode.LOCAL)
        blocks = [
            _block("a", score=10.0),
            _block("b", score=2.0),
            _block("c", score=0.5),
        ]
        result = p.apply_fusion_gates(blocks, query="any")
        # Gates ran: survivors have gate_* scores attached
        assert all("gate_fused_score" in b for b in result)

    def test_gate_log_written_to_disk(self, monkeypatch, tmp_path):
        """D-3: enabling gates must produce a gate-log JSONL record."""
        from pathlib import Path

        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        p = RecallPipeline(mode=PipelineMode.LOCAL)
        blocks = [_block("a", score=5.0), _block("b", score=1.0)]
        p.apply_fusion_gates(blocks, query="test query")

        gate_files = list((tmp_path / ".claude" / "depthfusion-metrics").glob("*-gates.jsonl"))
        assert len(gate_files) == 1
        content = gate_files[0].read_text()
        assert "fusion_gate" in content
        assert "test" not in content  # raw query must not be logged
        # query_hash IS logged (anonymised)
        import json as _json
        first_line = content.strip().split("\n")[0]
        entry = _json.loads(first_line)
        assert entry["event"] == "fusion_gate"
        assert entry["query_hash"]  # non-empty hash
        assert "log" in entry

    def test_fail_open_when_gates_reject_everything(self, monkeypatch, tmp_path):
        """If gates filter the entire pool, return original blocks (recall > gates)."""
        from pathlib import Path

        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        # Impossible threshold → everything fails Δ gate
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD", "9999999")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        p = RecallPipeline(mode=PipelineMode.LOCAL)
        blocks = [_block("a", score=1.0), _block("b", score=2.0)]
        result = p.apply_fusion_gates(blocks)
        # Fail-open: original blocks returned unchanged
        assert len(result) == 2
        assert [b["chunk_id"] for b in result] == ["a", "b"]


# ---------------------------------------------------------------------------
# TS parity matrix (AC-3) — 20 deterministic test cases
# ---------------------------------------------------------------------------

# Each case is: (name, blocks, query_embedding, config_kwargs, expected_survivor_ids)
# Encoded as the contract the Python port MUST satisfy — these are the
# deterministic outcomes the TS reference implementation also produces on
# identical inputs. Tests run the Python gate and assert the survivor set.

_PARITY_CASES: list[tuple] = [
    # 1. All blocks pass (permissive config)
    ("all_pass", [
        _block("a", 5.0), _block("b", 4.0), _block("c", 3.0),
    ], None, dict(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     ["a", "b", "c"]),

    # 2. B gate rejects bottom BM25
    ("b_rejects_bottom", [
        _block("top", 10.0), _block("bot", 0.1),
    ], None, dict(b_threshold=0.99, c_threshold=0.0, delta_threshold=0.0),
     ["top"]),

    # 3. Δ gate rejects below fused threshold (thresholds are on normalised
    # [0,1] fused_score — not raw BM25 — so a threshold of 0.5 keeps only
    # the top percentile in this 2-block pool).
    ("delta_rejects", [
        _block("big", 20.0), _block("small", 1.0),
    ], None, dict(alpha=0.0, b_threshold=0.0, c_threshold=0.0, delta_threshold=0.5),
     ["big"]),

    # 4. Empty input → empty output
    ("empty", [], None, dict(), []),

    # 5. Single block bypasses C gate
    ("single_bypasses_c", [_block("solo", 1.0)], None,
     dict(b_threshold=0.0, c_threshold=0.99, delta_threshold=0.0),
     ["solo"]),

    # 6. Query embedding: aligned block passes B, orthogonal fails
    ("embedding_b", [
        _block("aligned", 1.0, [1, 0, 0]),
        _block("orthog", 1.0, [0, 1, 0]),
    ], [1, 0, 0], dict(b_threshold=0.5, c_threshold=0.0, delta_threshold=0.0),
     ["aligned"]),

    # 7. Sorting: highest fused_score first
    ("sort_desc", [
        _block("lo", 1.0), _block("hi", 100.0), _block("mid", 50.0),
    ], None, dict(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     ["hi", "mid", "lo"]),

    # 8. Alpha=1.0 → fused = B only (ignores base_score)
    ("alpha_1_uses_b_only", [
        _block("low_bm25_high_sim", 0.1, [1, 0]),
        _block("hi_bm25_low_sim", 100.0, [0, 1]),
    ], [1, 0], dict(alpha=1.0, b_threshold=0.0, c_threshold=0.0, delta_threshold=0.5),
     ["low_bm25_high_sim"]),

    # 9. Alpha=0.0 → fused = base percentile only (ignores similarity).
    # Thresholds on the normalised [0,1] fused_score.
    ("alpha_0_uses_base_only", [
        _block("hi_bm25", 100.0, [0, 1]),
        _block("low_bm25", 0.1, [1, 0]),
    ], [1, 0], dict(alpha=0.0, b_threshold=0.0, c_threshold=0.0, delta_threshold=0.5),
     ["hi_bm25"]),

    # 10. C gate rejects topical orphan.
    # sib_b's [0.99, 0.01, 0] has marginally higher cosine vs [1,1,1] than
    # sib_a's [1, 0, 0] (the 0.01 contributes to the dot product), so
    # sib_b's fused_score is slightly higher.
    ("c_rejects_orphan", [
        _block("sib_a", 1.0, [1, 0, 0]),
        _block("sib_b", 1.0, [0.99, 0.01, 0]),
        _block("orphan", 1.0, [0, 0, 1]),
    ], [1, 1, 1], dict(b_threshold=0.0, c_threshold=0.5, delta_threshold=0.0),
     ["sib_b", "sib_a"]),

    # 11. Tied base_scores retain stable relative order
    ("ties_stable", [
        _block("first", 5.0), _block("second", 5.0), _block("third", 5.0),
    ], None, dict(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     ["first", "second", "third"]),  # stable sort (Python's sorted is stable)

    # 12. Zero-score block: allowed through with permissive config
    ("zero_score_ok_permissive", [
        _block("zero", 0.0), _block("pos", 1.0),
    ], None, dict(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     ["pos", "zero"]),

    # 13. Negative embedding → cosine is negative → B rejects.
    # b_threshold=0.0 means "b_score >= 0.0 required"; cos(query, -query) = -1.0
    # fails that predicate. Single-block exemption waives C only, not B.
    ("opposite_embedding", [
        _block("opposite", 1.0, [-1, 0]),
    ], [1, 0], dict(b_threshold=0.0, c_threshold=0.99, delta_threshold=0.0),
     []),

    # 14. When query has an embedding, the B gate uses cosine for all blocks —
    # blocks WITHOUT an embedding get b_score=0.0 (treated as "no similarity
    # signal"). With alpha=0.3 and large base_score disparity (10 vs 1), the
    # no_emb block still outranks the with_emb block by fused_score.
    # no_emb:   fused = 0.3*0.0 + 0.7*10.0 = 7.0
    # with_emb: fused = 0.3*1.0 + 0.7*1.0  = 1.0
    ("mixed_embeddings", [
        _block("no_emb", 10.0),
        _block("with_emb", 1.0, [1, 0]),
    ], [1, 0], dict(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     ["no_emb", "with_emb"]),

    # 15. All blocks identical embedding → C gate passes all (pairwise sim=1.0)
    ("all_identical_embeddings", [
        _block("a", 1.0, [1, 0, 0]),
        _block("b", 2.0, [1, 0, 0]),
        _block("c", 3.0, [1, 0, 0]),
    ], [1, 0, 0], dict(b_threshold=0.0, c_threshold=0.99, delta_threshold=0.0),
     ["c", "b", "a"]),

    # 16. delta_threshold exactly equal to fused_score → passes (>=).
    # Single block → percentile=1.0 (top in a one-element pool); alpha=0
    # → fused=1.0; delta=1.0 → 1.0 >= 1.0 passes.
    ("delta_exact_equal", [
        _block("exact", 10.0),
    ], None, dict(alpha=0.0, b_threshold=0.0, c_threshold=0.0, delta_threshold=1.0),
     ["exact"]),

    # 17. b_threshold exactly equal → passes (>=)
    ("b_exact_equal_percentile", [
        _block("only", 5.0),  # single block, percentile=1.0 with positive score
    ], None, dict(b_threshold=1.0, c_threshold=0.0, delta_threshold=0.0),
     ["only"]),

    # 18. Alpha in valid range, non-extreme
    ("alpha_0p3_blend", [
        _block("a", 10.0, [1, 0]), _block("b", 10.0, [0, 1]),
    ], [1, 0], dict(alpha=0.3, b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     # a: fused = 0.3*1.0 + 0.7*10.0 = 7.3
     # b: fused = 0.3*0.0 + 0.7*10.0 = 7.0
     ["a", "b"]),

    # 19. Malformed block (missing 'score') is treated as score=0
    ("missing_score_is_zero", [
        {"chunk_id": "no_score"}, _block("normal", 5.0),
    ], None, dict(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     ["normal", "no_score"]),  # normal has higher percentile

    # 20. Chunk_id missing → synthesised as "idxN"
    ("missing_chunk_id", [
        {"score": 5.0}, {"score": 1.0},
    ], None, dict(b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
     ["idx0", "idx1"]),
]


@pytest.mark.parametrize(
    "name,blocks,query_embedding,config_kwargs,expected_ids",
    _PARITY_CASES,
    ids=[c[0] for c in _PARITY_CASES],
)
def test_ts_parity_case(name, blocks, query_embedding, config_kwargs, expected_ids):
    """AC-3: each case encodes a TS-reference-compatible contract."""
    gates = SelectiveFusionGates(GateConfig(**config_kwargs))
    survivors, log = gates.apply(blocks, query_embedding=query_embedding)
    actual_ids = [b.get("chunk_id", f"idx{i}") for i, b in enumerate(survivors)]
    assert actual_ids == expected_ids, (
        f"[{name}] expected {expected_ids}, got {actual_ids}; log={log}"
    )


# ---------------------------------------------------------------------------
# GateDecision + GateLog invariants
# ---------------------------------------------------------------------------

class TestGateDecision:
    def test_passes_all_requires_all_three(self):
        d = GateDecision(
            chunk_id="x", passes_b=True, passes_c=True, passes_delta=True,
            b_score=1.0, c_score=1.0, base_score=1.0, fused_score=1.0,
        )
        assert d.passes_all is True

        d2 = GateDecision(
            chunk_id="x", passes_b=True, passes_c=True, passes_delta=False,
            b_score=0.0, c_score=0.0, base_score=0.0, fused_score=0.0,
        )
        assert d2.passes_all is False

    def test_gate_decision_is_frozen(self):
        from dataclasses import FrozenInstanceError
        d = GateDecision(
            chunk_id="x", passes_b=True, passes_c=True, passes_delta=True,
            b_score=1.0, c_score=1.0, base_score=1.0, fused_score=1.0,
        )
        with pytest.raises(FrozenInstanceError):
            d.passes_b = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Review-gate regressions (lock in the corrections from the code-reviewer)
# ---------------------------------------------------------------------------

class TestReviewGateRegressions:
    def test_blend_scale_consistent_b_and_base_both_in_unit_interval(self):
        """HIGH-1 fix: `base_score` is normalised to percentile [0,1] before
        the α blend, so α's semantic meaning ("how much weight does the B
        signal get?") is preserved regardless of raw BM25 magnitude. Without
        this, raw BM25=20 would dominate an α=0.3 blend and the B gate
        would be functionally invisible.
        """
        gates = SelectiveFusionGates(
            GateConfig(alpha=0.5, b_threshold=0.0, c_threshold=0.0, delta_threshold=0.0),
        )
        blocks = [_block("a", 1.0), _block("b", 100.0), _block("c", 0.5)]
        _, log = gates.apply(blocks)
        for dec in log.decisions:
            # fused_score must land in [0,1] under the normalised blend
            assert 0.0 <= dec.fused_score <= 1.0, (
                f"fused_score {dec.fused_score} escaped [0,1] — blend scale broken"
            )

    def test_sequential_gate_counters_form_a_funnel(self):
        """HIGH-2 fix: passed_c and passed_delta are counted only among
        blocks that passed every prior gate (funnel semantics).

        Without sequential counting, `passed_c` could exceed `passed_b`
        when a block passes C but fails B — making the log unreadable.
        """
        gates = SelectiveFusionGates(
            GateConfig(
                # B threshold is moderate so only some blocks pass it
                alpha=0.5, b_threshold=0.5, c_threshold=0.0, delta_threshold=0.0,
            ),
        )
        # 4 blocks, percentiles [0.0, 0.33, 0.66, 1.0] — top 2 pass B
        blocks = [_block(f"b{i}", float(i)) for i in range(4)]
        _, log = gates.apply(blocks)
        assert log.passed_b == 2
        assert log.passed_c <= log.passed_b    # funnel invariant
        assert log.passed_delta <= log.passed_c

    def test_per_block_embedding_fallback_when_query_has_embedding(self):
        """HIGH-3 fix: a block WITHOUT an embedding, in a query that HAS
        one, falls back to BM25 percentile for its B score — not the
        previous `b_score=0.0` penalty that would silently fail B.
        """
        gates = SelectiveFusionGates(
            GateConfig(
                alpha=0.5, b_threshold=0.6, c_threshold=0.0, delta_threshold=0.0,
            ),
        )
        blocks = [
            _block("no_emb_high_bm25", 100.0),      # no embedding, top BM25
            _block("emb_aligned", 0.1, [1, 0, 0]),  # embedding matches query
            _block("emb_orthog", 0.1, [0, 1, 0]),   # embedding orthogonal
        ]
        _, log = gates.apply(blocks, query_embedding=[1, 0, 0])
        decs = {d.chunk_id: d for d in log.decisions}
        # no_emb_high_bm25: percentile = 1.0 (top) → passes b_threshold=0.6
        assert decs["no_emb_high_bm25"].passes_b is True
        # emb_aligned: cos=1.0 → passes
        assert decs["emb_aligned"].passes_b is True
        # emb_orthog: cos=0.0 → fails
        assert decs["emb_orthog"].passes_b is False

    def test_numpy_floats_serialised_as_numbers_not_strings(self, tmp_path, monkeypatch):
        """MED-6 fix: numpy scalars in block scores must round-trip through
        JSONL as native numbers, not stringified `"0.3000000"`. The previous
        `default=str` would silently stringify them and corrupt downstream
        log parsers.
        """
        from pathlib import Path
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        # Skip if numpy unavailable (the coercion path still works — test
        # just can't exercise it directly).
        np = pytest.importorskip("numpy")

        from depthfusion.fusion.gates import GateLog
        from depthfusion.metrics.collector import MetricsCollector
        log = GateLog(
            alpha=np.float32(0.3),
            b_threshold=0.1, c_threshold=0.1, delta_threshold=0.0,
            total_candidates=1, passed_b=1, passed_c=1, passed_delta=1,
        )
        MetricsCollector(tmp_path).record_gate_log(log)

        gate_files = list(tmp_path.glob("*-gates.jsonl"))
        assert len(gate_files) == 1
        import json as _json
        entry = _json.loads(gate_files[0].read_text().strip())
        # alpha must be a number in the JSON, not a string
        assert isinstance(entry["log"]["alpha"], (int, float))

    def test_fallback_triggered_field_in_gate_log_entry(self, tmp_path, monkeypatch):
        """LOW-7 fix: when gates reject everything and the retrieval layer
        fails-open to original blocks, the gate-log entry MUST carry
        `fallback_triggered: true` so operators can see the override.
        """
        from pathlib import Path

        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD", "999")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        p = RecallPipeline(mode=PipelineMode.LOCAL)
        p.apply_fusion_gates(
            [_block("a", 1.0), _block("b", 2.0)],
            query="something",
        )

        gate_files = list((tmp_path / ".claude" / "depthfusion-metrics").glob("*-gates.jsonl"))
        assert len(gate_files) == 1
        import json as _json
        entry = _json.loads(gate_files[0].read_text().strip())
        assert entry["fallback_triggered"] is True


class TestGateLog:
    def test_counts_never_exceed_total(self):
        gates = SelectiveFusionGates(
            GateConfig(b_threshold=0.5, c_threshold=0.5, delta_threshold=0.5),
        )
        blocks = [_block(f"b{i}", float(i)) for i in range(10)]
        _, log = gates.apply(blocks)
        assert log.passed_b <= log.total_candidates
        assert log.passed_c <= log.total_candidates
        assert log.passed_delta <= log.total_candidates
        assert log.total_candidates == 10

    def test_gate_log_is_frozen(self):
        from dataclasses import FrozenInstanceError
        log = GateLog(
            alpha=0.3, b_threshold=0.1, c_threshold=0.1, delta_threshold=0.0,
            total_candidates=0, passed_b=0, passed_c=0, passed_delta=0,
        )
        with pytest.raises(FrozenInstanceError):
            log.alpha = 0.5  # type: ignore[misc]
