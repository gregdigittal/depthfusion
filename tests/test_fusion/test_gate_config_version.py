# tests/test_fusion/test_gate_config_version.py
"""GateConfig.version_id() + end-to-end I-8 wiring tests — S-58 / T-180.

AC-4: ≥ 4 new tests covering:
  - deterministic ID generation (same input → same hash)
  - ID changes when config changes (any field)
  - ID stable across calls with unchanged config
  - ID appears on disk in gate-log JSONL entry

Closes the S-51 TODO(I-8) marker by giving auditors a stable snapshot
pointer on every gate-log record.
"""
from __future__ import annotations

import json
from pathlib import Path

from depthfusion.fusion.gates import GateConfig

# ---------------------------------------------------------------------------
# Determinism + stability
# ---------------------------------------------------------------------------

class TestVersionIdDeterminism:
    def test_same_config_produces_same_id(self):
        """Identical configs must produce identical IDs (pure function)."""
        a = GateConfig(alpha=0.3, b_threshold=0.1, c_threshold=0.05, delta_threshold=0.0)
        b = GateConfig(alpha=0.3, b_threshold=0.1, c_threshold=0.05, delta_threshold=0.0)
        assert a.version_id() == b.version_id()

    def test_id_is_12_hex_chars(self):
        """Length + character set match the sha256[:12] contract."""
        cfg = GateConfig()
        vid = cfg.version_id()
        assert len(vid) == 12
        assert all(c in "0123456789abcdef" for c in vid)

    def test_stable_across_repeated_calls(self):
        """Repeated calls on the same instance produce the same ID (no clock
        dependency, no RNG, no global state).
        """
        cfg = GateConfig(alpha=0.42, b_threshold=0.2)
        ids = [cfg.version_id() for _ in range(5)]
        assert len(set(ids)) == 1

    def test_default_config_has_deterministic_id(self):
        """The default GateConfig's version_id is a known constant — pinning
        it in a test catches accidental default changes that would silently
        invalidate every historical gate-log entry.
        """
        cfg = GateConfig()
        # Computed from defaults alpha=0.30, b=0.10, c=0.05, delta=0.0 — any
        # change to the defaults or the hashing format will surface here.
        vid = cfg.version_id()
        assert len(vid) == 12
        # Recompute via the documented contract to double-check
        import hashlib
        expected_raw = (
            f"alpha={0.30:.10f}|b_threshold={0.10:.10f}|"
            f"c_threshold={0.05:.10f}|delta_threshold={0.0:.10f}"
        )
        expected = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()[:12]
        assert vid == expected


# ---------------------------------------------------------------------------
# Sensitivity — any field change → new ID
# ---------------------------------------------------------------------------

class TestVersionIdSensitivity:
    def test_alpha_change_produces_different_id(self):
        a = GateConfig(alpha=0.30)
        b = GateConfig(alpha=0.31)
        assert a.version_id() != b.version_id()

    def test_b_threshold_change_produces_different_id(self):
        a = GateConfig(b_threshold=0.10)
        b = GateConfig(b_threshold=0.11)
        assert a.version_id() != b.version_id()

    def test_c_threshold_change_produces_different_id(self):
        a = GateConfig(c_threshold=0.05)
        b = GateConfig(c_threshold=0.06)
        assert a.version_id() != b.version_id()

    def test_delta_threshold_change_produces_different_id(self):
        a = GateConfig(delta_threshold=0.0)
        b = GateConfig(delta_threshold=0.5)
        assert a.version_id() != b.version_id()

    def test_post_init_clamping_normalizes_pre_clamp_values(self):
        """Two configs with different pre-clamp values but identical
        post-clamp values produce the same ID. The ID reflects the
        EFFECTIVE config, not the caller's input.
        """
        # Both clamp to alpha=1.0
        a = GateConfig(alpha=1.5)
        b = GateConfig(alpha=99.0)
        assert a.alpha == 1.0 == b.alpha
        assert a.version_id() == b.version_id()

    def test_signed_zero_normalized(self):
        """Review-gate regression: `alpha=-0.0` and `alpha=0.0` compare equal
        (0.0 == -0.0 is True in Python) — their version_ids must match.

        Python's `max(0.0, min(1.0, -0.0))` happens to return +0.0 due to
        first-arg-wins on IEEE 754 tie, but we don't rely on that: the
        version_id() function also normalises signed zero as defense in
        depth against future interpreter changes.
        """
        a = GateConfig(alpha=-0.0, b_threshold=-0.0, c_threshold=-0.0)
        b = GateConfig(alpha=0.0, b_threshold=0.0, c_threshold=0.0)
        # Configs compare equal
        assert a.alpha == b.alpha
        # And their IDs agree
        assert a.version_id() == b.version_id()

    def test_signed_zero_on_delta_threshold_normalized(self):
        """delta_threshold's `max(0.0, ...)` clamp would return +0.0 for -0.0,
        but if a future refactor removes that clamp, version_id() still
        collapses the sign bit.
        """
        a = GateConfig(delta_threshold=-0.0)
        b = GateConfig(delta_threshold=0.0)
        assert a.version_id() == b.version_id()


# ---------------------------------------------------------------------------
# End-to-end: ID appears on the JSONL gate-log record
# ---------------------------------------------------------------------------

class TestVersionIdReachesDisk:
    def test_config_version_id_written_to_gate_log_entry(self, tmp_path, monkeypatch):
        """I-8: the config_version_id must be present in every gate-log
        record on disk, ready for audit replay against the snapshot that
        produced each decision.
        """
        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        p = RecallPipeline(mode=PipelineMode.LOCAL)
        blocks = [
            {"chunk_id": "a", "score": 5.0},
            {"chunk_id": "b", "score": 1.0},
        ]
        p.apply_fusion_gates(blocks, query="anything")

        gate_files = list((tmp_path / ".claude" / "depthfusion-metrics").glob("*-gates.jsonl"))
        assert len(gate_files) == 1
        entry = json.loads(gate_files[0].read_text().strip())
        # config_version_id must be present, non-empty, and match the
        # default GateConfig's id
        assert "config_version_id" in entry
        assert entry["config_version_id"] == GateConfig().version_id()

    def test_config_version_id_changes_when_env_var_changes(self, tmp_path, monkeypatch):
        """Two recall invocations with different DEPTHFUSION_FUSION_GATES_ALPHA
        values produce gate-log records with different config_version_ids —
        auditors can diff the two to explain behaviour changes.
        """
        from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        p = RecallPipeline(mode=PipelineMode.LOCAL)
        blocks = [{"chunk_id": "x", "score": 1.0}, {"chunk_id": "y", "score": 2.0}]

        # First invocation with default alpha
        p.apply_fusion_gates(blocks, query="first")

        # Second invocation after env var changes
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ALPHA", "0.99")
        p.apply_fusion_gates(blocks, query="second")

        gate_file = next((tmp_path / ".claude" / "depthfusion-metrics").glob("*-gates.jsonl"))
        entries = [json.loads(ln) for ln in gate_file.read_text().splitlines() if ln.strip()]
        assert len(entries) == 2
        # Same query would produce the same query_hash; differentiate by
        # config_version_id
        assert entries[0]["config_version_id"] != entries[1]["config_version_id"]
        # Second one matches explicit alpha=0.99
        expected = GateConfig(alpha=0.99).version_id()
        assert entries[1]["config_version_id"] == expected
