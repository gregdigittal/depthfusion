"""Tests for E-27 / S-70 — separate `importance` and `salience` scalars.

≥ 8 tests required by S-70 AC-5. This file is the TDD red phase: it imports
names that won't exist until T-220b lands (`MemoryScore`, `DEFAULT_IMPORTANCE`,
`DEFAULT_SALIENCE`, `extract_memory_score`). Tests are skip-gated on those
imports so the rest of the suite still runs cleanly during the red phase.

Acceptance criteria coverage map:
- AC-1 (defaults & ranges):    Test_DefaultsAndBounds (4 tests)
- AC-2 (extractor derivation): Test_ExtractorImportance (3 tests)
- AC-2 (publish path):         Test_PublishContextScalar (1 test)
- AC-3 (backward compat):      Test_BackwardCompat (1 test)
- AC-4 (set_memory_score):     Test_SetMemoryScoreTool (3 tests)
- AC-5 (≥ 8 tests):            12 tests total
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip-gated imports for T-220b (Commit 2 — schema + parse layer)
# ---------------------------------------------------------------------------

try:
    from depthfusion.core.types import (
        DEFAULT_IMPORTANCE,
        DEFAULT_SALIENCE,
        MemoryScore,
    )
    SCORING_TYPES_AVAILABLE = True
except ImportError:
    SCORING_TYPES_AVAILABLE = False
    DEFAULT_IMPORTANCE = 0.5
    DEFAULT_SALIENCE = 1.0
    MemoryScore = None  # type: ignore[assignment]

try:
    from depthfusion.capture.dedup import extract_memory_score
    PARSE_AVAILABLE = True
except ImportError:
    PARSE_AVAILABLE = False
    extract_memory_score = None  # type: ignore[assignment]

# Skip-gate for T-220b (Commit 2)
scoring_types_required = pytest.mark.skipif(
    not SCORING_TYPES_AVAILABLE,
    reason="S-70 T-220b types not yet implemented",
)
parse_required = pytest.mark.skipif(
    not PARSE_AVAILABLE,
    reason="S-70 T-220b dedup parse helper not yet implemented",
)

# Skip-gate for T-221 (Commit 3 — extractor wiring).
#
# Consensus-driven design (Round 1, both reviewers): the previous version
# of this gate only required types+parse imports, but the *test bodies*
# write a discovery file via the extractor and then assert that the
# written frontmatter contains the new keys. If T-220b lands (types+parse
# available) but T-221 has not (extractors not yet emitting `importance`),
# these tests would fail rather than skip. We probe the *behavior* of
# write_decisions() against a tmp_path the first time the gate is checked
# and cache the result, so the gate reflects whether the wiring is live.
def _probe_extractor_wiring_lazy() -> bool:
    """Return True iff write_decisions() emits the importance frontmatter key.

    Behavioral probe (not just import-existence) — protects against the
    types+parse landing without the extractor wiring. Cached after first
    call. Errors during probing → False (treat as not-yet-wired).
    """
    if not (SCORING_TYPES_AVAILABLE and PARSE_AVAILABLE):
        return False
    try:
        import tempfile
        from pathlib import Path as _P
        from depthfusion.capture.decision_extractor import (
            DecisionEntry as _DE,
            write_decisions as _wd,
        )
        with tempfile.TemporaryDirectory() as td:
            out = _wd(
                [_DE("probe", confidence=0.5)],
                project="probe", session_id="probe",
                output_dir=_P(td),
            )
            if out is None:
                return False
            return "importance:" in out.read_text(encoding="utf-8")
    except Exception:
        return False

EXTRACTOR_WIRING_AVAILABLE = _probe_extractor_wiring_lazy()
extractor_wiring_required = pytest.mark.skipif(
    not EXTRACTOR_WIRING_AVAILABLE,
    reason="S-70 T-221 extractor wiring not yet active",
)

# Skip-gate for T-223 (Commit 3 — set_memory_score MCP tool).
# Consensus-driven design: this gate also requires PARSE_AVAILABLE because
# the test bodies call `extract_memory_score(...)` to verify writes. Without
# the combined gate, T-223 landing before T-220b parse would crash the tests
# (calling None(body) → TypeError) instead of skipping cleanly.
try:
    from depthfusion.mcp.server import _tool_set_memory_score
    SET_TOOL_AVAILABLE = True
except ImportError:
    SET_TOOL_AVAILABLE = False
    _tool_set_memory_score = None  # type: ignore[assignment]
set_tool_required = pytest.mark.skipif(
    not (SET_TOOL_AVAILABLE and PARSE_AVAILABLE),
    reason="S-70 T-223 set_memory_score tool requires T-220b parse helper",
)

# Skip-gate for T-222 (Commit 3 — publish_context importance plumbing).
# Behavioral probe: does `_tool_publish_context` actually persist an
# `importance` field when one is supplied? `_tool_publish_context` already
# exists pre-S-70 (S-78), so a simple import probe would falsely report
# "available" before T-222 lands.
def _probe_publish_wiring_lazy() -> bool:
    if not SCORING_TYPES_AVAILABLE:
        return False
    try:
        import inspect as _inspect
        from depthfusion.core.types import ContextItem as _CI
        sig = _inspect.signature(_CI)
        return "importance" in sig.parameters
    except Exception:
        return False

PUBLISH_WIRING_AVAILABLE = _probe_publish_wiring_lazy()
publish_wiring_required = pytest.mark.skipif(
    not PUBLISH_WIRING_AVAILABLE,
    reason="S-70 T-222 publish_context importance plumbing not yet active",
)


# ---------------------------------------------------------------------------
# AC-1: Defaults and bounds
# ---------------------------------------------------------------------------

@scoring_types_required
class TestDefaultsAndBounds:
    """AC-1: importance ∈ [0.0, 1.0] default 0.5; salience ∈ [0.0, 5.0] default 1.0."""

    def test_default_values(self):
        """AC-1: MemoryScore() yields the canonical defaults."""
        s = MemoryScore()
        assert s.importance == DEFAULT_IMPORTANCE == 0.5
        assert s.salience == DEFAULT_SALIENCE == 1.0

    @pytest.mark.parametrize(
        "raw_importance,expected",
        [
            (-0.5, 0.0),
            (-1e-9, 0.0),
            (-0.0, 0.0),       # negative-zero special form
            (0.0, 0.0),
            (1.0, 1.0),
            (1.0001, 1.0),
            (10.0, 1.0),
        ],
    )
    def test_importance_clamped_to_unit_interval(self, raw_importance, expected):
        """AC-1: importance values outside [0.0, 1.0] are clamped silently.

        Includes -0.0 (Python float special form). NaN/Inf cases are
        tested separately because their handling requires a defined
        contract (clamp-to-default), not silent passthrough.
        """
        s = MemoryScore(importance=raw_importance)
        assert s.importance == expected

    @pytest.mark.parametrize(
        "raw_salience,expected",
        [
            (-2.0, 0.0),
            (0.0, 0.0),
            (0.001, 0.001),    # near-zero in-range fence-post
            (4.999, 4.999),    # near-max in-range fence-post
            (5.0, 5.0),
            (5.0001, 5.0),
            (100.0, 5.0),
        ],
    )
    def test_salience_clamped_to_zero_to_five(self, raw_salience, expected):
        """AC-1: salience values outside [0.0, 5.0] are clamped silently."""
        s = MemoryScore(salience=raw_salience)
        assert s.salience == expected

    @pytest.mark.parametrize(
        "non_finite",
        [float("nan"), float("inf"), float("-inf")],
    )
    def test_non_finite_importance_clamps_to_default(self, non_finite):
        """AC-1: NaN/Inf/-Inf for importance are NOT silently passed through.

        Python's `min`/`max` propagate NaN. A naive `max(0, min(1, val))`
        would let NaN through unchanged. The contract: any non-finite
        value collapses to the canonical default 0.5 — not raised, not NaN,
        not 0.0/1.0 (which would imply meaningful clamping of nonsense).
        """
        import math
        s = MemoryScore(importance=non_finite)
        assert math.isfinite(s.importance), \
            f"importance must be finite after construction, got {s.importance!r}"
        assert s.importance == DEFAULT_IMPORTANCE

    @pytest.mark.parametrize(
        "non_finite",
        [float("nan"), float("inf"), float("-inf")],
    )
    def test_non_finite_salience_clamps_to_default(self, non_finite):
        """AC-1: NaN/Inf/-Inf for salience collapse to DEFAULT_SALIENCE.

        Same rationale as test_non_finite_importance_clamps_to_default.
        """
        import math
        s = MemoryScore(salience=non_finite)
        assert math.isfinite(s.salience), \
            f"salience must be finite after construction, got {s.salience!r}"
        assert s.salience == DEFAULT_SALIENCE

    def test_explicit_in_range_values_preserved(self):
        """AC-1: legitimate values inside the range are not mutated."""
        s = MemoryScore(importance=0.73, salience=2.5)
        assert s.importance == 0.73
        assert s.salience == 2.5


# ---------------------------------------------------------------------------
# AC-2: Extractors derive importance from confidence
# ---------------------------------------------------------------------------

@extractor_wiring_required
class TestExtractorImportance:
    """AC-2: decision_extractor / negative_extractor / auto_learn paths must
    emit `importance` (= confidence) and `salience` (= default) into the
    discovery frontmatter."""

    def test_decision_extractor_writes_scalars(self, tmp_path):
        """AC-2: write_decisions() emits importance derived from confidence."""
        from depthfusion.capture.decision_extractor import (
            DecisionEntry,
            write_decisions,
        )
        entry = DecisionEntry("Use redis for sessions", confidence=0.83)
        out = write_decisions(
            [entry], project="testproj", session_id="sess-1",
            output_dir=tmp_path,
        )
        assert out is not None and out.exists()
        body = out.read_text(encoding="utf-8")
        score = extract_memory_score(body)
        assert score is not None
        assert score.importance == pytest.approx(0.83, abs=1e-3)
        assert score.salience == DEFAULT_SALIENCE

    def test_negative_extractor_writes_scalars(self, tmp_path):
        """AC-2: write_negatives() emits importance derived from confidence."""
        from depthfusion.capture.negative_extractor import (
            NegativeEntry,
            write_negatives,
        )
        entry = NegativeEntry(
            what="Avoid caching auth tokens in localStorage",
            why="XSS exposure",
            confidence=0.91,
        )
        out = write_negatives(
            [entry], project="testproj", session_id="sess-2",
            output_dir=tmp_path,
        )
        assert out is not None and out.exists()
        body = out.read_text(encoding="utf-8")
        score = extract_memory_score(body)
        assert score is not None
        assert score.importance == pytest.approx(0.91, abs=1e-3)
        assert score.salience == DEFAULT_SALIENCE

    def test_aggregate_importance_uses_max_confidence(self, tmp_path):
        """AC-2: file-level importance = MAX of per-entry confidences.

        DESIGN CHOICE (deliberate, reviewable): when multiple entries are
        written into one discovery file, the file's frontmatter `importance`
        is set from the MAX of per-entry confidences — "loudest signal wins."

        Alternatives considered:
          - MEAN: dilutes one high-stakes capture with adjacent low-stakes
            ones. A 0.95 confidence rule next to a 0.4 heuristic would
            score 0.675, defeating the lifecycle policy that's meant to
            preserve high-importance items.
          - LAST or FIRST: positional, not principled.
          - WEIGHTED MEAN: requires per-entry weight metadata not yet
            present in the schema.

        The MAX rule preserves high-importance items at file granularity
        while leaving per-entry confidence visible in the body for any
        future per-entry policy. Revisit if S-71 (decay buckets) or S-72
        (recall feedback) need a different aggregator.
        """
        from depthfusion.capture.decision_extractor import (
            DecisionEntry,
            write_decisions,
        )
        entries = [
            DecisionEntry("low-confidence note", confidence=0.4),
            DecisionEntry("high-confidence rule", confidence=0.95),
            DecisionEntry("medium note", confidence=0.7),
        ]
        out = write_decisions(
            entries, project="testproj", session_id="sess-3",
            output_dir=tmp_path,
        )
        assert out is not None
        body = out.read_text(encoding="utf-8")
        score = extract_memory_score(body)
        assert score is not None
        assert score.importance == pytest.approx(0.95, abs=1e-3)


# ---------------------------------------------------------------------------
# AC-2: publish_context accepts operator-supplied importance
# ---------------------------------------------------------------------------

@publish_wiring_required
class TestPublishContextScalar:
    """AC-2: publish_context accepts an optional `importance` field; clamps
    out-of-range values defensively. Gated on T-222 wiring (see
    `_probe_publish_wiring_lazy` above) so this test stays skipped between
    Commit 2 (types only) and Commit 3 (publish wiring)."""

    def test_publish_context_accepts_optional_importance(self, tmp_path, monkeypatch):
        """AC-2: explicit importance flows through to the persisted item."""
        # Use an isolated bus path so the test does not pollute the user's
        # home dir, mirroring the S-78 test_bus_idempotency pattern.
        monkeypatch.setenv("DEPTHFUSION_BUS_DIR", str(tmp_path))

        # Force a fresh bus singleton for this test.
        from depthfusion.mcp import server as mcp_server
        mcp_server._BUS_INSTANCE = None  # type: ignore[attr-defined]

        result_text = mcp_server._tool_publish_context(
            {
                "item": {
                    "item_id": "it-1",
                    "content": "context payload",
                    "source_agent": "tester",
                    "tags": ["s70"],
                    "importance": 0.88,
                },
            },
            config=None,
        )
        import json as _json
        result = _json.loads(result_text)
        assert result.get("published") is True
        assert "error" not in result

        # The written bus row must carry the importance scalar.
        bus_file = tmp_path / "bus.jsonl"
        assert bus_file.exists()
        rows = [_json.loads(line) for line in bus_file.read_text().splitlines() if line.strip()]
        assert any(
            float(row.get("importance", -1.0)) == pytest.approx(0.88, abs=1e-3)
            for row in rows
        )


# ---------------------------------------------------------------------------
# AC-3: Backward compatibility with old discovery files
# ---------------------------------------------------------------------------

@parse_required
class TestBackwardCompat:
    """AC-3: a discovery file with no scoring frontmatter must read back as
    canonical defaults (no migration required)."""

    def test_old_file_yields_defaults(self, tmp_path):
        """AC-3: a frontmatter without importance/salience parses to defaults.

        Contract (decided in consensus review): `extract_memory_score()`
        returns a `MemoryScore` populated with canonical defaults when the
        frontmatter is silent. It MUST NOT return None — that would push
        defaulting onto every caller and recreate the bug we're trying to
        prevent (a caller that forgets to default would silently get
        unscored items into recall).
        """
        body = (
            "---\n"
            "project: legacy\n"
            "session_id: old-sess\n"
            "type: decisions\n"
            "---\n"
            "\n"
            "# Legacy file written before S-70\n"
            "- Some old decision\n"
        )
        score = extract_memory_score(body)
        assert score is not None, \
            "extract_memory_score must return canonical defaults, never None"
        assert score.importance == DEFAULT_IMPORTANCE
        assert score.salience == DEFAULT_SALIENCE

    def test_body_text_does_not_spoof_score(self):
        """AC-3 (consensus-driven, Round 1 of Commit 2): body text mentioning
        `importance:` or `salience:` must NOT be parsed as the file's score.

        Discoveries are markdown documents; their bodies frequently quote
        scores in prose ("the importance: 0.9 rating from S-71..."). The
        regex parser must scope to the YAML frontmatter block only —
        otherwise a body line silently spoofs the file's score.
        """
        body = (
            "---\n"
            "project: legit\n"
            "type: decisions\n"
            "---\n"
            "\n"
            "# Discovery\n"
            "\n"
            "Notes on prior captures:\n"
            "- importance: 0.99\n"   # body line — must NOT be parsed
            "- salience: 4.99\n"     # body line — must NOT be parsed
            "\n"
        )
        score = extract_memory_score(body)
        assert score is not None
        assert score.importance == DEFAULT_IMPORTANCE, \
            f"body-text spoofing detected: importance={score.importance}"
        assert score.salience == DEFAULT_SALIENCE, \
            f"body-text spoofing detected: salience={score.salience}"

    @pytest.mark.parametrize(
        "raw_imp,raw_sal",
        [
            ("not-a-number", "1.0"),
            ("0.7", "five"),
            ("nan", "1.0"),
            ("inf", "1.0"),
            ("-inf", "-1"),
            ("", ""),
        ],
    )
    def test_malformed_scalars_fall_back_to_defaults(self, raw_imp, raw_sal):
        """AC-3 (consensus-driven): malformed legacy frontmatter values must
        not crash the parser. Hand-edited or corrupted files containing
        `importance: not-a-number` or `salience: five` must resolve to
        canonical defaults — no exception, no NaN/Inf bleed-through.
        Mirrors the S-78 malformed-row guard precedent.
        """
        body = (
            "---\n"
            "project: legacy\n"
            f"importance: {raw_imp}\n"
            f"salience: {raw_sal}\n"
            "type: decisions\n"
            "---\n"
            "\n"
            "# Discovery with malformed scoring fields\n"
        )
        score = extract_memory_score(body)
        # Either canonical defaults or a clamped finite value — never an
        # exception, never NaN/Inf, never a non-MemoryScore return.
        import math
        assert score is not None
        assert math.isfinite(score.importance)
        assert math.isfinite(score.salience)
        assert 0.0 <= score.importance <= 1.0
        assert 0.0 <= score.salience <= 5.0


# ---------------------------------------------------------------------------
# AC-4: depthfusion_set_memory_score MCP tool
# ---------------------------------------------------------------------------

@set_tool_required
class TestSetMemoryScoreTool:
    """AC-4: idempotent overrides; structured error on missing file."""

    def _seed_discovery(self, tmp_path: Path) -> Path:
        f = tmp_path / "2026-05-01-acme-decisions.md"
        f.write_text(
            "---\n"
            "project: acme\n"
            "session_id: sess-x\n"
            "type: decisions\n"
            "importance: 0.5\n"
            "salience: 1.0\n"
            "---\n"
            "\n"
            "# Decisions\n"
            "- Some decision\n",
            encoding="utf-8",
        )
        return f

    def test_set_memory_score_partial_update(self, tmp_path):
        """AC-4: supplying only importance leaves salience untouched."""
        f = self._seed_discovery(tmp_path)
        result_text = _tool_set_memory_score(
            {"filename": str(f), "importance": 0.92}
        )
        import json as _json
        result = _json.loads(result_text)
        assert result.get("ok") is True
        assert "error" not in result

        body = f.read_text(encoding="utf-8")
        score = extract_memory_score(body)
        assert score is not None
        assert score.importance == pytest.approx(0.92, abs=1e-3)
        assert score.salience == pytest.approx(1.0, abs=1e-3)  # unchanged

    def test_set_memory_score_idempotent(self, tmp_path):
        """AC-4: calling twice with the same values is a no-op (no corruption)."""
        f = self._seed_discovery(tmp_path)
        import json as _json

        first = _tool_set_memory_score(
            {"filename": str(f), "importance": 0.7, "salience": 2.5}
        )
        body_after_first = f.read_text(encoding="utf-8")

        second = _tool_set_memory_score(
            {"filename": str(f), "importance": 0.7, "salience": 2.5}
        )
        body_after_second = f.read_text(encoding="utf-8")

        assert _json.loads(first).get("ok") is True
        assert _json.loads(second).get("ok") is True
        assert body_after_first == body_after_second

    def test_set_memory_score_missing_file_returns_error(self, tmp_path):
        """AC-4: missing file → structured error, not exception, no file created."""
        ghost = tmp_path / "does-not-exist.md"
        result_text = _tool_set_memory_score(
            {"filename": str(ghost), "importance": 0.5}
        )
        import json as _json
        result = _json.loads(result_text)
        assert result.get("ok") is False
        assert "error" in result
        assert not ghost.exists()

    def test_set_memory_score_concurrent_writes_no_corruption(self, tmp_path):
        """AC-4 (consensus-driven, mirrors S-78 torn-write coverage):
        concurrent set_memory_score calls must not produce a partial-write
        residue. The final file must parse cleanly to a valid MemoryScore;
        the persisted values must equal one of the writers' inputs (last
        winner wins) — never a mix of fields from different writers.
        """
        import json as _json
        import threading
        f = self._seed_discovery(tmp_path)

        # Two writers race with disjoint values. After both finish, the
        # file must be a valid discovery with importance/salience taken
        # from exactly one writer (atomic-rename guarantee).
        writers = [
            {"filename": str(f), "importance": 0.10, "salience": 0.5},
            {"filename": str(f), "importance": 0.90, "salience": 4.5},
        ]
        results: list[dict] = []
        results_lock = threading.Lock()

        def _run(payload: dict) -> None:
            text = _tool_set_memory_score(payload)
            with results_lock:
                results.append(_json.loads(text))

        threads = [threading.Thread(target=_run, args=(p,)) for p in writers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All calls must have returned cleanly (no exceptions surfaced as
        # missing entries).
        assert len(results) == 2
        assert all(r.get("ok") is True for r in results), \
            f"unexpected error in concurrent set: {results}"

        # Final file must still parse and the values must come from one
        # writer's payload (no field mixing).
        body = f.read_text(encoding="utf-8")
        score = extract_memory_score(body)
        assert score is not None
        valid_pairs = {(0.10, 0.5), (0.90, 4.5)}
        actual_pair = (
            round(score.importance, 4),
            round(score.salience, 4),
        )
        assert actual_pair in valid_pairs, \
            f"field mixing detected: got {actual_pair}, expected one of {valid_pairs}"
