"""Unit tests for scripts/ciqs_compare.py — two-mode delta reports."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


@pytest.fixture(scope="module")
def cmp():
    """Load scripts/ciqs_compare.py as a module."""
    path = SCRIPTS_DIR / "ciqs_compare.py"
    spec = importlib.util.spec_from_file_location("ciqs_compare", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ciqs_compare"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# bootstrap_delta_ci
# --------------------------------------------------------------------------

class TestBootstrapDeltaCI:
    def test_empty_baseline_returns_nan(self, cmp):
        low, high = cmp.bootstrap_delta_ci([], [1.0, 2.0])
        assert low != low and high != high  # NaN

    def test_empty_candidate_returns_nan(self, cmp):
        low, high = cmp.bootstrap_delta_ci([1.0, 2.0], [])
        assert low != low and high != high

    def test_both_singleton_returns_exact_delta(self, cmp):
        low, high = cmp.bootstrap_delta_ci([5.0], [8.0])
        assert low == 3.0 and high == 3.0

    def test_identical_sets_delta_near_zero(self, cmp):
        values = [50.0, 55.0, 45.0, 52.0, 48.0]
        low, high = cmp.bootstrap_delta_ci(values, values.copy())
        # Bootstrap of identical sets with independent resampling should
        # straddle zero; exact bounds depend on seed but CI should contain 0
        assert low <= 0.0 <= high

    def test_candidate_clearly_better_positive_delta(self, cmp):
        baseline = [10.0, 12.0, 11.0, 9.0, 13.0]  # mean ~11
        candidate = [50.0, 52.0, 51.0, 49.0, 53.0]  # mean ~51
        low, high = cmp.bootstrap_delta_ci(baseline, candidate)
        # Delta should be ~+40, well above zero
        assert low > 30.0
        assert high < 50.0

    def test_candidate_clearly_worse_negative_delta(self, cmp):
        baseline = [80.0, 82.0, 78.0]
        candidate = [20.0, 22.0, 18.0]
        low, high = cmp.bootstrap_delta_ci(baseline, candidate)
        # Delta should be ~-60
        assert high < -40.0

    def test_seed_determinism(self, cmp):
        baseline = [30.0, 40.0, 35.0]
        candidate = [45.0, 55.0, 50.0]
        first = cmp.bootstrap_delta_ci(baseline, candidate, seed=42)
        second = cmp.bootstrap_delta_ci(baseline, candidate, seed=42)
        assert first == second

    def test_confidence_validation(self, cmp):
        with pytest.raises(ValueError):
            cmp.bootstrap_delta_ci([1.0, 2.0], [3.0, 4.0], confidence=1.5)

    def test_wider_confidence_wider_ci(self, cmp):
        baseline = [30.0, 40.0, 35.0]
        candidate = [45.0, 55.0, 50.0]
        narrow = cmp.bootstrap_delta_ci(baseline, candidate, confidence=0.80)
        wide = cmp.bootstrap_delta_ci(baseline, candidate, confidence=0.99)
        assert (wide[1] - wide[0]) >= (narrow[1] - narrow[0])


# --------------------------------------------------------------------------
# classify_delta
# --------------------------------------------------------------------------

class TestClassifyDelta:
    def test_improved_when_low_above_zero(self, cmp):
        assert cmp.classify_delta(1.0, 5.0) == "improved"

    def test_regressed_when_high_below_zero(self, cmp):
        assert cmp.classify_delta(-5.0, -1.0) == "regressed"

    def test_parity_when_ci_spans_zero(self, cmp):
        assert cmp.classify_delta(-2.0, 2.0) == "parity"

    def test_parity_at_exact_zero_boundary(self, cmp):
        assert cmp.classify_delta(0.0, 5.0) == "parity"
        assert cmp.classify_delta(-5.0, 0.0) == "parity"

    def test_nan_returns_insufficient_data(self, cmp):
        nan = float("nan")
        assert cmp.classify_delta(nan, nan) == "insufficient-data"
        assert cmp.classify_delta(nan, 5.0) == "insufficient-data"


# --------------------------------------------------------------------------
# format_comparison_report
# --------------------------------------------------------------------------

class TestFormatReport:
    def test_header_contains_both_labels(self, cmp):
        report = cmp.format_comparison_report(
            baseline_per_cat={"A": [80.0, 82.0]},
            candidate_per_cat={"A": [85.0, 87.0]},
            baseline_label="vps-cpu",
            candidate_label="vps-gpu",
            baseline_files=[Path("a.jsonl")],
            candidate_files=[Path("b.jsonl")],
        )
        assert "vps-cpu" in report
        assert "vps-gpu" in report
        assert "Comparison" in report

    def test_shows_run_counts(self, cmp):
        report = cmp.format_comparison_report(
            baseline_per_cat={"A": [80.0]},
            candidate_per_cat={"A": [85.0]},
            baseline_label="b",
            candidate_label="c",
            baseline_files=[Path("a.jsonl"), Path("b.jsonl"), Path("c.jsonl")],
            candidate_files=[Path("d.jsonl"), Path("e.jsonl")],
        )
        assert "3 runs" in report
        assert "2 runs" in report

    def test_improvement_summary(self, cmp):
        report = cmp.format_comparison_report(
            baseline_per_cat={"A": [10.0, 11.0, 12.0]},
            candidate_per_cat={"A": [80.0, 81.0, 82.0]},
            baseline_label="old",
            candidate_label="new",
            baseline_files=[Path("a.jsonl")],
            candidate_files=[Path("b.jsonl")],
        )
        assert "Net improvement" in report
        assert "improved" in report

    def test_regression_warning(self, cmp):
        report = cmp.format_comparison_report(
            baseline_per_cat={"A": [80.0, 82.0, 81.0]},
            candidate_per_cat={"A": [10.0, 12.0, 11.0]},
            baseline_label="old",
            candidate_label="new",
            baseline_files=[Path("a.jsonl")],
            candidate_files=[Path("b.jsonl")],
        )
        assert "regressions detected" in report.lower()
        assert "⚠" in report or "regressed" in report.lower()

    def test_missing_category_handled(self, cmp):
        # Category B exists only in candidate; A only in baseline
        report = cmp.format_comparison_report(
            baseline_per_cat={"A": [80.0, 82.0]},
            candidate_per_cat={"B": [85.0, 87.0]},
            baseline_label="old",
            candidate_label="new",
            baseline_files=[Path("a.jsonl")],
            candidate_files=[Path("b.jsonl")],
        )
        assert "insufficient-data" in report

    def test_delta_sign_shown(self, cmp):
        report = cmp.format_comparison_report(
            baseline_per_cat={"A": [50.0, 52.0, 48.0]},
            candidate_per_cat={"A": [70.0, 72.0, 68.0]},
            baseline_label="b",
            candidate_label="c",
            baseline_files=[Path("a.jsonl")],
            candidate_files=[Path("b.jsonl")],
        )
        # Delta ~+20 should appear as +20.0
        assert "+20.0" in report or "+2" in report

    def test_ci_uses_configured_confidence_in_header(self, cmp):
        report = cmp.format_comparison_report(
            baseline_per_cat={"A": [80.0, 82.0]},
            candidate_per_cat={"A": [85.0, 87.0]},
            baseline_label="b",
            candidate_label="c",
            baseline_files=[Path("a.jsonl")],
            candidate_files=[Path("b.jsonl")],
            confidence=0.90,
        )
        assert "90%" in report


# --------------------------------------------------------------------------
# End-to-end via main()
# --------------------------------------------------------------------------

class TestMainCLI:
    def _write_scored(self, path: Path, records: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_e2e_compare_two_sets(self, cmp, tmp_path):
        # Baseline: 2 runs of Category A, lower scores
        b1 = tmp_path / "b1.jsonl"
        b2 = tmp_path / "b2.jsonl"
        self._write_scored(b1, [
            {"category_id": "A", "topic_id": "t1",
             "scores": {"d1": 5, "d2": 5, "d3": 6}}
        ])
        self._write_scored(b2, [
            {"category_id": "A", "topic_id": "t2",
             "scores": {"d1": 6, "d2": 5, "d3": 5}}
        ])
        # Candidate: 2 runs, higher scores
        c1 = tmp_path / "c1.jsonl"
        c2 = tmp_path / "c2.jsonl"
        self._write_scored(c1, [
            {"category_id": "A", "topic_id": "t1",
             "scores": {"d1": 9, "d2": 9, "d3": 10}}
        ])
        self._write_scored(c2, [
            {"category_id": "A", "topic_id": "t2",
             "scores": {"d1": 10, "d2": 9, "d3": 9}}
        ])

        out = tmp_path / "report.md"
        rc = cmp.main([
            "--baseline-label", "old",
            "--candidate-label", "new",
            "--baseline", str(b1), str(b2),
            "--candidate", str(c1), str(c2),
            "--out", str(out),
        ])
        assert rc == 0
        content = out.read_text()
        assert "improved" in content
        assert "old" in content and "new" in content

    def test_e2e_exit_nonzero_on_regression(self, cmp, tmp_path):
        # Baseline >> candidate → regression
        b1 = tmp_path / "b1.jsonl"
        c1 = tmp_path / "c1.jsonl"
        self._write_scored(b1, [
            {"category_id": "A", "topic_id": "t1",
             "scores": {"d1": 9, "d2": 10, "d3": 10}},
            {"category_id": "A", "topic_id": "t2",
             "scores": {"d1": 10, "d2": 9, "d3": 9}},
        ])
        self._write_scored(c1, [
            {"category_id": "A", "topic_id": "t1",
             "scores": {"d1": 2, "d2": 1, "d3": 2}},
            {"category_id": "A", "topic_id": "t2",
             "scores": {"d1": 1, "d2": 2, "d3": 1}},
        ])
        rc = cmp.main([
            "--baseline-label", "b",
            "--candidate-label", "c",
            "--baseline", str(b1),
            "--candidate", str(c1),
            "--exit-nonzero-on-regression",
            "--out", str(tmp_path / "r.md"),
        ])
        assert rc == 2  # nonzero exit on regression
