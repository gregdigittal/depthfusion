"""Unit tests for scripts/ciqs_summarise.py math and
scripts/ciqs_harness.py template parsing.

We import each module by file path because scripts/ is not on the
package path.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def summ():
    """Load scripts/ciqs_summarise.py as a module."""
    path = SCRIPTS_DIR / "ciqs_summarise.py"
    spec = importlib.util.spec_from_file_location("ciqs_summarise", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ciqs_summarise"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# percentile
# --------------------------------------------------------------------------

class TestPercentile:
    def test_empty_returns_nan(self, summ):
        assert math.isnan(summ.percentile([], 50.0))

    def test_singleton_returns_value(self, summ):
        assert summ.percentile([42.0], 50.0) == 42.0
        assert summ.percentile([42.0], 0.0) == 42.0
        assert summ.percentile([42.0], 100.0) == 42.0

    def test_boundary_percentiles(self, summ):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert summ.percentile(vals, 0.0) == 1.0
        assert summ.percentile(vals, 100.0) == 5.0

    def test_median(self, summ):
        # p50 of [1,2,3,4,5] interpolates to index 2 = 3.0
        assert summ.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50.0) == 3.0

    def test_quartiles_linear_interp(self, summ):
        vals = [1.0, 2.0, 3.0, 4.0]
        # p25: index (4-1)*0.25 = 0.75 -> 1.0 + 0.75*(2.0-1.0) = 1.75
        assert summ.percentile(vals, 25.0) == pytest.approx(1.75)
        # p75: index (4-1)*0.75 = 2.25 -> 3.0 + 0.25*(4.0-3.0) = 3.25
        assert summ.percentile(vals, 75.0) == pytest.approx(3.25)

    def test_out_of_range_raises(self, summ):
        with pytest.raises(ValueError):
            summ.percentile([1.0, 2.0], -1.0)
        with pytest.raises(ValueError):
            summ.percentile([1.0, 2.0], 101.0)

    def test_order_independence(self, summ):
        # percentile should sort internally
        a = summ.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50.0)
        b = summ.percentile([5.0, 1.0, 3.0, 2.0, 4.0], 50.0)
        assert a == b


# --------------------------------------------------------------------------
# bootstrap_ci
# --------------------------------------------------------------------------

class TestBootstrapCI:
    def test_empty_returns_nan_pair(self, summ):
        low, high = summ.bootstrap_ci([])
        assert math.isnan(low)
        assert math.isnan(high)

    def test_single_value_collapses(self, summ):
        low, high = summ.bootstrap_ci([7.0])
        assert low == 7.0
        assert high == 7.0

    def test_constant_data_zero_width(self, summ):
        # If all samples are 5.0, every bootstrap mean is 5.0
        low, high = summ.bootstrap_ci([5.0] * 10)
        assert low == pytest.approx(5.0)
        assert high == pytest.approx(5.0)

    def test_ci_contains_mean(self, summ):
        # With reasonable data, the CI should bracket the sample mean
        vals = [50.0, 60.0, 55.0, 65.0, 58.0]
        mean = sum(vals) / len(vals)
        low, high = summ.bootstrap_ci(vals, n_resamples=2000, seed=7)
        assert low <= mean <= high

    def test_seed_determinism(self, summ):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        low1, high1 = summ.bootstrap_ci(vals, seed=42, n_resamples=1000)
        low2, high2 = summ.bootstrap_ci(vals, seed=42, n_resamples=1000)
        assert low1 == low2
        assert high1 == high2

    def test_wider_data_wider_ci(self, summ):
        tight = [50.0, 51.0, 49.0, 50.5, 49.5]
        wide = [10.0, 90.0, 40.0, 70.0, 30.0]
        low_t, high_t = summ.bootstrap_ci(tight, seed=1, n_resamples=1000)
        low_w, high_w = summ.bootstrap_ci(wide, seed=1, n_resamples=1000)
        assert (high_t - low_t) < (high_w - low_w)

    def test_confidence_bound_check(self, summ):
        with pytest.raises(ValueError):
            summ.bootstrap_ci([1.0, 2.0], confidence=0.0)
        with pytest.raises(ValueError):
            summ.bootstrap_ci([1.0, 2.0], confidence=1.0)


# --------------------------------------------------------------------------
# normalise_topic_score
# --------------------------------------------------------------------------

class TestNormaliseTopicScore:
    def test_all_max(self, summ):
        # 4 dims, each at max 10 -> sum 40, max 40 -> 100.0
        assert summ.normalise_topic_score({"a": 10, "b": 10, "c": 10, "d": 10}, 40) == 100.0

    def test_all_zero(self, summ):
        assert summ.normalise_topic_score({"a": 0, "b": 0}, 20) == 0.0

    def test_half(self, summ):
        assert summ.normalise_topic_score({"a": 5, "b": 5}, 20) == 50.0

    def test_zero_max_guards(self, summ):
        assert summ.normalise_topic_score({"a": 5}, 0) == 0.0

    def test_negative_max_guards(self, summ):
        # Defensive: max should never be negative, but don't crash
        assert summ.normalise_topic_score({"a": 5}, -10) == 0.0


# --------------------------------------------------------------------------
# group_by_category
# --------------------------------------------------------------------------

class TestGroupByCategory:
    def test_groups_and_normalises(self, summ):
        recs = [
            {"category_id": "A", "topic_id": "A1", "scores": {"r": 10, "s": 10, "c": 10, "n": 10}},
            {"category_id": "A", "topic_id": "A2", "scores": {"r": 5, "s": 5, "c": 5, "n": 5}},
            {"category_id": "B", "topic_id": "B1", "scores": {"x": 8, "y": 8, "z": 8, "w": 8}},
        ]
        out = summ.group_by_category(recs)
        assert set(out.keys()) == {"A", "B"}
        assert out["A"] == [100.0, 50.0]
        assert out["B"] == [80.0]

    def test_skips_unscored(self, summ):
        recs = [
            {"category_id": "A", "topic_id": "A1", "scores": None},
            {"category_id": "A", "topic_id": "A2", "scores": {"r": 10, "s": 10}},
        ]
        out = summ.group_by_category(recs)
        assert "A" in out
        assert out["A"] == [100.0]

    def test_variable_dim_count(self, summ):
        # Category E has 3 dims (max 30); the function infers max from len(scores)
        recs = [
            {"category_id": "E", "topic_id": "E1", "scores": {"a": 10, "b": 10, "c": 10}},
        ]
        out = summ.group_by_category(recs)
        assert out["E"] == [100.0]


# --------------------------------------------------------------------------
# format_report smoke
# --------------------------------------------------------------------------

class TestFormatReport:
    def test_nonempty_contains_category_row(self, summ):
        per_cat = {"A": [80.0, 85.0, 90.0], "B": [70.0, 72.0, 68.0]}
        report = summ.format_report(per_cat, "local", [Path("/tmp/fake.jsonl")])
        assert "CIQS Summary - local" in report
        assert "| A | 3 |" in report
        assert "| B | 3 |" in report
        assert "Unweighted composite" in report

    def test_empty_produces_no_composite(self, summ):
        report = summ.format_report({}, "local", [])
        assert "CIQS Summary - local" in report
        assert "Unweighted composite" not in report
