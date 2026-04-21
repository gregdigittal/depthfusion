"""Unit tests for scripts/ciqs_weekly.py — autonomous regression detection."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


@pytest.fixture(scope="module")
def weekly():
    path = SCRIPTS_DIR / "ciqs_weekly.py"
    spec = importlib.util.spec_from_file_location("ciqs_weekly", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ciqs_weekly"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Fake aggregator — returns pre-scripted per-day data
# --------------------------------------------------------------------------

class _FakeAggregator:
    def __init__(
        self,
        backend_by_date: dict[date, dict] | None = None,
        capture_by_date: dict[date, dict] | None = None,
    ) -> None:
        self._backend = backend_by_date or {}
        self._capture = capture_by_date or {}

    def backend_summary(self, target_date):
        return self._backend.get(target_date, {})

    def capture_summary(self, target_date):
        return self._capture.get(target_date, {})


def _make_backend_day(
    per_backend: dict[str, dict],
    total_queries: int,
    total_errors: int = 0,
) -> dict:
    """Shape matches the real backend_summary() return shape."""
    return {
        "per_backend": per_backend,
        "per_capability_fallback": {},
        "total_queries": total_queries,
        "total_errors": total_errors,
        "overall_error_rate": total_errors / total_queries if total_queries else 0.0,
    }


def _make_capture_day(per_mechanism: dict[str, int]) -> dict:
    return {
        "per_mechanism": {
            mech: {"total": n} for mech, n in per_mechanism.items()
        },
    }


# --------------------------------------------------------------------------
# collect_window
# --------------------------------------------------------------------------

class TestCollectWindow:
    def test_empty_aggregator_returns_zeros(self, weekly):
        agg = _FakeAggregator()
        result = weekly.collect_window(agg, date(2026, 4, 21), window_days=7)
        assert result["total_queries"] == 0
        assert result["total_errors"] == 0
        assert result["capture_write_total"] == 0
        assert result["days_with_recall_data"] == 0

    def test_single_day_of_data_aggregates(self, weekly):
        d = date(2026, 4, 21)
        agg = _FakeAggregator(
            backend_by_date={
                d: _make_backend_day(
                    {"reranker::gemma": {
                        "count": 10, "measured_count": 10,
                        "avg_latency_ms": 150.0,
                        "p50_latency_ms": 140.0,
                        "p95_latency_ms": 200.0,
                        "error_count": 0,
                        "error_rate": 0.0,
                    }},
                    total_queries=10,
                )
            },
            capture_by_date={d: _make_capture_day({"decision": 5, "commit": 3})},
        )
        result = weekly.collect_window(agg, d, window_days=7)
        assert result["total_queries"] == 10
        assert result["days_with_recall_data"] == 1
        assert "reranker::gemma" in result["per_backend"]
        assert result["per_backend"]["reranker::gemma"]["count"] == 10
        assert result["capture_write_total"] == 8

    def test_multi_day_window_sums_per_backend(self, weekly):
        end = date(2026, 4, 21)
        agg = _FakeAggregator(
            backend_by_date={
                end - timedelta(days=i): _make_backend_day(
                    {"reranker::gemma": {
                        "count": 10, "measured_count": 10,
                        "avg_latency_ms": 100.0 + i * 10,
                        "p50_latency_ms": 90.0,
                        "p95_latency_ms": 200.0 + i * 20,
                        "error_count": i,  # 0, 1, 2 errors
                        "error_rate": i / 10,
                    }},
                    total_queries=10, total_errors=i,
                )
                for i in range(3)
            },
        )
        result = weekly.collect_window(agg, end, window_days=7)
        assert result["per_backend"]["reranker::gemma"]["count"] == 30
        assert result["per_backend"]["reranker::gemma"]["days_observed"] == 3
        # error_rate_avg = mean of per-day error rates: (0 + 0.1 + 0.2) / 3
        assert result["per_backend"]["reranker::gemma"]["error_rate_avg"] == pytest.approx(0.1)

    def test_p95_samples_excludes_zero(self, weekly):
        # A day with no latency samples (all timeouts) should not
        # contribute a 0 to the p95_ms_samples list
        d = date(2026, 4, 21)
        agg = _FakeAggregator(
            backend_by_date={
                d: _make_backend_day(
                    {"reranker::gemma": {
                        "count": 5, "measured_count": 0,  # all errors
                        "avg_latency_ms": 0.0, "p50_latency_ms": 0.0,
                        "p95_latency_ms": 0.0, "error_count": 5,
                        "error_rate": 1.0,
                    }},
                    total_queries=5, total_errors=5,
                )
            },
        )
        result = weekly.collect_window(agg, d, window_days=1)
        slot = result["per_backend"]["reranker::gemma"]
        assert slot["p95_ms_samples"] == []  # zero p95 not counted
        assert slot["p95_ms_avg"] == 0.0
        # But count/error tracking still works
        assert slot["error_count"] == 5


# --------------------------------------------------------------------------
# detect_regressions
# --------------------------------------------------------------------------

def _window(per_backend: dict, capture_total: int = 0,
            days: int = 7, captures_by_mechanism: dict[str, int] | None = None) -> dict:
    return {
        "per_backend": per_backend,
        "total_queries": sum(b.get("count", 0) for b in per_backend.values()),
        "total_errors": sum(b.get("error_count", 0) for b in per_backend.values()),
        "overall_error_rate": 0.0,
        "captures_by_mechanism": captures_by_mechanism or {},
        "capture_write_total": capture_total,
        "days_with_recall_data": days,
    }


class TestDetectRegressions:
    def test_no_change_no_findings(self, weekly):
        cur = _window({"a::b": {
            "count": 100, "error_count": 1, "error_rate_avg": 0.01,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }})
        base = _window({"a::b": {
            "count": 100, "error_count": 1, "error_rate_avg": 0.01,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }})
        findings = weekly.detect_regressions(cur, base)
        assert findings == []

    def test_latency_regression_detected(self, weekly):
        cur = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 300.0, "p95_ms_samples": [300.0],
        }})
        base = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }})
        findings = weekly.detect_regressions(cur, base, latency_pct_threshold=0.20)
        latency_findings = [f for f in findings if f["kind"] == "latency"]
        assert len(latency_findings) == 1
        assert latency_findings[0]["subject"] == "a::b"
        assert "+100.0%" in latency_findings[0]["delta"]
        # 100% increase is > 2x threshold (20%) → severity alert
        assert latency_findings[0]["severity"] == "alert"

    def test_latency_within_threshold_no_finding(self, weekly):
        cur = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 170.0, "p95_ms_samples": [170.0],  # +13%
        }})
        base = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }})
        findings = weekly.detect_regressions(cur, base, latency_pct_threshold=0.20)
        assert [f for f in findings if f["kind"] == "latency"] == []

    def test_error_rate_regression(self, weekly):
        cur = _window({"a::b": {
            "count": 100, "error_count": 15, "error_rate_avg": 0.15,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }})
        base = _window({"a::b": {
            "count": 100, "error_count": 1, "error_rate_avg": 0.01,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }})
        findings = weekly.detect_regressions(cur, base, error_rate_pp_threshold=0.05)
        err_findings = [f for f in findings if f["kind"] == "error_rate"]
        assert len(err_findings) == 1
        # 14pp increase > 2 * 5pp → alert
        assert err_findings[0]["severity"] == "alert"

    def test_capture_volume_drop(self, weekly):
        cur = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }}, capture_total=50)
        base = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }}, capture_total=200)
        findings = weekly.detect_regressions(cur, base, capture_volume_pct_threshold=0.30)
        cap_findings = [f for f in findings if f["kind"] == "capture_volume"]
        assert len(cap_findings) == 1
        # 75% drop > 2 * 30% → alert
        assert cap_findings[0]["severity"] == "alert"

    def test_availability_drop(self, weekly):
        cur = _window({"a::b": {
            "count": 10, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }}, days=3)  # only 3 of 7 days had data
        base = _window({"a::b": {
            "count": 10, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }}, days=7)
        findings = weekly.detect_regressions(cur, base)
        avail_findings = [f for f in findings if f["kind"] == "availability"]
        assert len(avail_findings) == 1

    def test_availability_respects_window_days(self, weekly):
        # Review-gate H-2 regression: with window_days=14, a 13/14
        # availability (93%) must flag as below the 95% threshold.
        # Before the fix, `/ 7` hardcoded produced 13/7 = 1.86 which
        # passed the threshold, suppressing the real regression.
        cur = _window({"a::b": {
            "count": 10, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }}, days=13)
        base = _window({"a::b": {
            "count": 10, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 150.0, "p95_ms_samples": [150.0],
        }}, days=14)
        # Both windows are 14 days
        cur["window_days"] = 14
        base["window_days"] = 14
        findings = weekly.detect_regressions(cur, base)
        avail_findings = [f for f in findings if f["kind"] == "availability"]
        assert len(avail_findings) == 1
        # And at 14/14 both ways, no regression
        cur["days_with_recall_data"] = 14
        findings2 = weekly.detect_regressions(cur, base)
        assert [f for f in findings2 if f["kind"] == "availability"] == []

    def test_new_backend_not_flagged_as_regression(self, weekly):
        # A backend that only appears in current (first time seen)
        # should NOT be flagged — there's no baseline to compare against
        cur = _window({"c::d": {
            "count": 100, "error_count": 50, "error_rate_avg": 0.5,
            "p95_ms_avg": 1000.0, "p95_ms_samples": [1000.0],
        }})
        base = _window({})
        findings = weekly.detect_regressions(cur, base)
        backend_findings = [f for f in findings if f["subject"] == "c::d"]
        assert backend_findings == []

    def test_multiple_backends_flagged_independently(self, weekly):
        cur = _window({
            "a::b": {
                "count": 100, "error_count": 0, "error_rate_avg": 0.0,
                "p95_ms_avg": 400.0, "p95_ms_samples": [400.0],
            },
            "c::d": {
                "count": 100, "error_count": 0, "error_rate_avg": 0.0,
                "p95_ms_avg": 200.0, "p95_ms_samples": [200.0],  # no change
            },
        })
        base = _window({
            "a::b": {
                "count": 100, "error_count": 0, "error_rate_avg": 0.0,
                "p95_ms_avg": 100.0, "p95_ms_samples": [100.0],
            },
            "c::d": {
                "count": 100, "error_count": 0, "error_rate_avg": 0.0,
                "p95_ms_avg": 200.0, "p95_ms_samples": [200.0],
            },
        })
        findings = weekly.detect_regressions(cur, base)
        ab = [f for f in findings if f["subject"] == "a::b"]
        cd = [f for f in findings if f["subject"] == "c::d"]
        assert len(ab) == 1
        assert cd == []


# --------------------------------------------------------------------------
# format_report
# --------------------------------------------------------------------------

class TestFormatReport:
    def test_no_regressions_shows_checkmark(self, weekly):
        cur = _window({})
        base = _window({})
        report = weekly.format_report(cur, base, [], date(2026, 4, 21))
        assert "No regressions detected" in report
        assert "✓" in report

    def test_regressions_shown_in_table(self, weekly):
        cur = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 400.0, "p95_ms_samples": [400.0],
        }})
        base = _window({"a::b": {
            "count": 100, "error_count": 0, "error_rate_avg": 0.0,
            "p95_ms_avg": 100.0, "p95_ms_samples": [100.0],
        }})
        findings = weekly.detect_regressions(cur, base)
        report = weekly.format_report(cur, base, findings, date(2026, 4, 21))
        assert "Regressions" in report
        assert "a::b" in report
        assert "⚠" in report or "alert" in report.lower() or "warn" in report.lower()

    def test_mechanical_only_disclaimer_present(self, weekly):
        cur = _window({})
        base = _window({})
        report = weekly.format_report(cur, base, [], date(2026, 4, 21))
        # The disclaimer about not auto-scoring quality MUST be present,
        # so operators don't conflate "no regression" with "no quality issues"
        assert "mechanical" in report.lower()
        assert "quality" in report.lower()

    def test_window_dates_shown(self, weekly):
        cur = _window({})
        base = _window({})
        report = weekly.format_report(cur, base, [], date(2026, 4, 21), window_days=7)
        assert "2026-04-21" in report
        assert "2026-04-15" in report  # 6 days earlier


# --------------------------------------------------------------------------
# main() exit codes
# --------------------------------------------------------------------------

class TestMainExitCodes:
    def test_exit_zero_when_no_data_and_no_regressions(self, weekly, monkeypatch, tmp_path):
        # Force aggregator to return empty for everything
        from depthfusion.metrics.collector import MetricsCollector
        from depthfusion.metrics.aggregator import MetricsAggregator

        class _Empty(MetricsAggregator):
            def backend_summary(self, target_date=None):
                return {}
            def capture_summary(self, target_date=None):
                return {}

        monkeypatch.setattr(
            "depthfusion.metrics.aggregator.MetricsAggregator",
            lambda *_args, **_kw: _Empty(MetricsCollector()),
        )
        out = tmp_path / "report.md"
        rc = weekly.main([
            "--end-date", "2026-04-21",
            "--out", str(out),
        ])
        assert rc == 0
        assert out.exists()

    def test_invalid_date_returns_2(self, weekly):
        rc = weekly.main(["--end-date", "not-a-date"])
        assert rc == 2
