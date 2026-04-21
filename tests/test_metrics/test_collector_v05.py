# tests/test_metrics/test_collector_v05.py
"""v0.5 structured metrics collector + aggregator tests — S-53 / T-165.

AC-3: ≥ 4 new tests. Covers:
  - record_recall_query + record_capture_event write structured JSONL
  - event_subtype validation (unknown → "ok")
  - backend_summary aggregation (per-backend latency + error rates)
  - capture_summary aggregation (per-mechanism write rates)
  - unknown capture_mechanism flagged but preserved on disk
  - config_version_id threading from I-11
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from depthfusion.metrics.aggregator import MetricsAggregator, _percentile
from depthfusion.metrics.collector import (
    _VALID_CAPTURE_MECHANISMS,
    _VALID_EVENT_SUBTYPES,
    MetricsCollector,
)

# ---------------------------------------------------------------------------
# Constants surface
# ---------------------------------------------------------------------------

class TestConstants:
    def test_event_subtypes_include_dr018_sla_expiry(self):
        """DR-018 I-19 ratification: sla_expiry_deny is a distinct subtype."""
        assert "sla_expiry_deny" in _VALID_EVENT_SUBTYPES

    def test_capture_mechanisms_cover_v05_cms(self):
        """All five v0.5 capture mechanisms are enumerated."""
        for cm in (
            "decision_extractor", "negative_extractor", "dedup",
            "git_post_commit", "confirm_discovery",
        ):
            assert cm in _VALID_CAPTURE_MECHANISMS


# ---------------------------------------------------------------------------
# record_recall_query
# ---------------------------------------------------------------------------

class TestRecordRecallQuery:
    def test_writes_structured_record(self, tmp_path):
        c = MetricsCollector(tmp_path)
        c.record_recall_query(
            query_hash="abc123",
            mode="vps-cpu",
            backend_used={"reranker": "haiku", "embedding": "null"},
            backend_fallback_chain={"embedding": ["local", "null"]},
            latency_ms_per_capability={"reranker": 42.0, "embedding": 1.0},
            total_latency_ms=50.0,
            result_count=5,
            event_subtype="ok",
            config_version_id="cfg12abc",
        )
        entry = _read_one(c.today_recall_path())
        assert entry["event"] == "recall_query"
        assert entry["event_subtype"] == "ok"
        assert entry["query_hash"] == "abc123"
        assert entry["mode"] == "vps-cpu"
        assert entry["backend_used"]["reranker"] == "haiku"
        assert entry["latency_ms_per_capability"]["reranker"] == 42.0
        assert entry["total_latency_ms"] == 50.0
        assert entry["result_count"] == 5
        assert entry["config_version_id"] == "cfg12abc"

    def test_unknown_event_subtype_coerces_to_ok(self, tmp_path):
        """Unknown subtypes are silently normalised, not written raw."""
        c = MetricsCollector(tmp_path)
        c.record_recall_query(event_subtype="completely-made-up-subtype")
        entry = _read_one(c.today_recall_path())
        assert entry["event_subtype"] == "ok"

    def test_all_valid_subtypes_preserved(self, tmp_path):
        """Each value in _VALID_EVENT_SUBTYPES round-trips intact."""
        c = MetricsCollector(tmp_path)
        for subtype in _VALID_EVENT_SUBTYPES:
            c.record_recall_query(event_subtype=subtype)
        lines = c.today_recall_path().read_text().splitlines()
        actual = {json.loads(ln)["event_subtype"] for ln in lines}
        assert actual == _VALID_EVENT_SUBTYPES

    def test_missing_optional_fields_render_as_empty(self, tmp_path):
        """Calling with no backend/latency dicts produces empty {} not null."""
        c = MetricsCollector(tmp_path)
        c.record_recall_query(query_hash="x")
        entry = _read_one(c.today_recall_path())
        assert entry["backend_used"] == {}
        assert entry["backend_fallback_chain"] == {}
        assert entry["latency_ms_per_capability"] == {}

    def test_recall_stream_separate_from_gate_stream(self, tmp_path):
        """recall.jsonl and gates.jsonl are distinct files."""
        c = MetricsCollector(tmp_path)
        c.record_recall_query(query_hash="a")
        # Only -recall.jsonl exists
        files = sorted(p.name for p in tmp_path.glob("*.jsonl"))
        assert any("-recall.jsonl" in f for f in files)
        assert not any("-gates.jsonl" in f for f in files)


# ---------------------------------------------------------------------------
# record_capture_event
# ---------------------------------------------------------------------------

class TestRecordCaptureEvent:
    def test_writes_structured_record(self, tmp_path):
        c = MetricsCollector(tmp_path)
        c.record_capture_event(
            capture_mechanism="decision_extractor",
            project="depthfusion",
            session_id="sess-abc",
            write_success=True,
            entries_written=3,
            file_path="/fake/path.md",
        )
        entry = _read_one(c.today_capture_path())
        assert entry["event"] == "capture"
        assert entry["capture_mechanism"] == "decision_extractor"
        assert entry["capture_mechanism_known"] is True
        assert entry["project"] == "depthfusion"
        assert entry["entries_written"] == 3
        assert entry["write_success"] is True

    def test_unknown_mechanism_flagged_but_preserved(self, tmp_path):
        """An unknown mechanism name is written to disk but flagged so the
        aggregator can bucket it separately for forensics.
        """
        c = MetricsCollector(tmp_path)
        c.record_capture_event(
            capture_mechanism="mystery_new_extractor",
            project="p",
            entries_written=1,
        )
        entry = _read_one(c.today_capture_path())
        assert entry["capture_mechanism"] == "mystery_new_extractor"
        assert entry["capture_mechanism_known"] is False

    def test_failure_records_success_false(self, tmp_path):
        c = MetricsCollector(tmp_path)
        c.record_capture_event(
            capture_mechanism="dedup",
            project="p",
            write_success=False,
            event_subtype="error",
        )
        entry = _read_one(c.today_capture_path())
        assert entry["write_success"] is False
        assert entry["event_subtype"] == "error"


# ---------------------------------------------------------------------------
# MetricsAggregator.backend_summary
# ---------------------------------------------------------------------------

class TestBackendSummary:
    def test_empty_file_returns_empty_dict(self, tmp_path):
        c = MetricsCollector(tmp_path)
        agg = MetricsAggregator(c)
        assert agg.backend_summary() == {}

    def test_aggregates_per_backend_latency(self, tmp_path):
        c = MetricsCollector(tmp_path)
        for lat in (10.0, 20.0, 30.0, 40.0):
            c.record_recall_query(
                backend_used={"reranker": "haiku"},
                latency_ms_per_capability={"reranker": lat},
            )
        agg = MetricsAggregator(c)
        summary = agg.backend_summary()
        key = "reranker::haiku"
        assert summary["per_backend"][key]["count"] == 4
        assert summary["per_backend"][key]["avg_latency_ms"] == 25.0
        assert summary["per_backend"][key]["p50_latency_ms"] == 20.0
        # p95 on 4 values: ceil(0.95*4)-1 = ceil(3.8)-1 = 3, so max
        assert summary["per_backend"][key]["p95_latency_ms"] == 40.0

    def test_error_rate_counts_non_ok_subtypes(self, tmp_path):
        c = MetricsCollector(tmp_path)
        c.record_recall_query(backend_used={"reranker": "haiku"}, event_subtype="ok",
                              latency_ms_per_capability={"reranker": 10.0})
        c.record_recall_query(backend_used={"reranker": "haiku"}, event_subtype="timeout",
                              latency_ms_per_capability={"reranker": 999.0})
        c.record_recall_query(backend_used={"reranker": "haiku"}, event_subtype="error",
                              latency_ms_per_capability={"reranker": 5.0})
        agg = MetricsAggregator(c)
        summary = agg.backend_summary()
        key = "reranker::haiku"
        assert summary["per_backend"][key]["count"] == 3
        assert summary["per_backend"][key]["error_count"] == 2
        assert summary["per_backend"][key]["error_rate"] == pytest.approx(2 / 3)
        assert summary["overall_error_rate"] == pytest.approx(2 / 3)

    def test_fallback_chain_collected_across_queries(self, tmp_path):
        c = MetricsCollector(tmp_path)
        c.record_recall_query(backend_fallback_chain={"embedding": ["local", "null"]})
        c.record_recall_query(backend_fallback_chain={"embedding": ["local"]})
        agg = MetricsAggregator(c)
        summary = agg.backend_summary()
        assert set(summary["per_capability_fallback"]["embedding"]) == {"local", "null"}

    def test_multiple_capabilities_tracked_separately(self, tmp_path):
        c = MetricsCollector(tmp_path)
        c.record_recall_query(
            backend_used={"reranker": "haiku", "embedding": "local"},
            latency_ms_per_capability={"reranker": 10.0, "embedding": 5.0},
        )
        agg = MetricsAggregator(c)
        summary = agg.backend_summary()
        assert "reranker::haiku" in summary["per_backend"]
        assert "embedding::local" in summary["per_backend"]


# ---------------------------------------------------------------------------
# MetricsAggregator.capture_summary
# ---------------------------------------------------------------------------

class TestCaptureSummary:
    def test_empty_returns_empty(self, tmp_path):
        c = MetricsCollector(tmp_path)
        assert MetricsAggregator(c).capture_summary() == {}

    def test_per_mechanism_write_rate(self, tmp_path):
        c = MetricsCollector(tmp_path)
        # 3 successful writes, 1 failure for decision_extractor
        for i, success in enumerate((True, True, True, False)):
            c.record_capture_event(
                capture_mechanism="decision_extractor",
                project="p", session_id=f"s{i}",
                write_success=success, entries_written=2 if success else 0,
            )
        agg = MetricsAggregator(c)
        summary = agg.capture_summary()
        de = summary["per_mechanism"]["decision_extractor"]
        assert de["total"] == 4
        assert de["successes"] == 3
        assert de["failures"] == 1
        assert de["write_rate"] == pytest.approx(0.75)
        assert de["entries_written"] == 6

    def test_unknown_mechanisms_surfaced(self, tmp_path):
        c = MetricsCollector(tmp_path)
        c.record_capture_event(capture_mechanism="weird_new_cm", project="p")
        c.record_capture_event(capture_mechanism="dedup", project="p")
        agg = MetricsAggregator(c)
        summary = agg.capture_summary()
        assert summary["unknown_mechanisms"] == ["weird_new_cm"]
        assert "weird_new_cm" in summary["per_mechanism"]
        assert "dedup" in summary["per_mechanism"]

    def test_historical_date_read_back(self, tmp_path):
        """backend_summary/capture_summary accept a past date — useful for
        nightly digests that run the morning after.
        """
        c = MetricsCollector(tmp_path)
        c.record_capture_event(capture_mechanism="dedup", project="p")
        # Read today's data back via the explicit date argument
        agg = MetricsAggregator(c)
        summary = agg.capture_summary(target_date=date.today())
        assert summary["per_mechanism"]["dedup"]["total"] == 1
        # Yesterday has no data
        summary_yesterday = agg.capture_summary(target_date=date.today() - timedelta(days=1))
        assert summary_yesterday == {}


# ---------------------------------------------------------------------------
# Review-gate regressions
# ---------------------------------------------------------------------------

class TestReviewGateRegressions:
    def test_unknown_event_subtype_logs_debug(self, tmp_path, caplog):
        """HIGH-2: coercion to 'ok' emits a DEBUG log so operators can spot
        typos without losing the event.
        """
        import logging
        caplog.set_level(logging.DEBUG, logger="depthfusion.metrics.collector")
        c = MetricsCollector(tmp_path)
        c.record_recall_query(event_subtype="timout")  # typo of "timeout"
        # The record still lands on disk with subtype coerced to "ok"...
        entry = _read_one(c.today_recall_path())
        assert entry["event_subtype"] == "ok"
        # ...but a DEBUG log names the offending subtype so the caller bug
        # isn't completely silent.
        assert any(
            "timout" in record.message and "coerced" in record.message
            for record in caplog.records
        )

    def test_backend_error_counted_even_without_latency(self, tmp_path):
        """MED-4: a backend that errored AND has no latency entry still
        shows up in per_backend with error_count/error_rate set.
        Previously, only backends with a recorded latency appeared in
        per_backend, so timeout-path errors invisibly inflated
        overall_error_rate without a bucket to attribute them to.
        """
        c = MetricsCollector(tmp_path)
        # One healthy call with latency, one timeout call without latency
        c.record_recall_query(
            backend_used={"reranker": "haiku"},
            latency_ms_per_capability={"reranker": 10.0},
            event_subtype="ok",
        )
        c.record_recall_query(
            backend_used={"reranker": "haiku"},
            latency_ms_per_capability={},  # no latency recorded
            event_subtype="timeout",
        )
        summary = MetricsAggregator(c).backend_summary()
        key = "reranker::haiku"
        # Both queries counted toward this backend
        assert summary["per_backend"][key]["count"] == 2
        # Only one latency sample
        assert summary["per_backend"][key]["measured_count"] == 1
        assert summary["per_backend"][key]["avg_latency_ms"] == 10.0
        # Error rate = 1/2 (not 1/1 or 0)
        assert summary["per_backend"][key]["error_count"] == 1
        assert summary["per_backend"][key]["error_rate"] == pytest.approx(0.5)
        assert summary["total_errors"] == 1

    def test_per_backend_measured_count_distinct_from_count(self, tmp_path):
        """MED-4 corollary: `count` and `measured_count` may diverge when
        some queries don't record latency. Both are surfaced so operators
        can reason about both error rate (over `count`) and latency stats
        (over `measured_count`).
        """
        c = MetricsCollector(tmp_path)
        # 3 queries, 2 with latency, 1 without (error path)
        for lat in (5.0, 15.0):
            c.record_recall_query(
                backend_used={"reranker": "haiku"},
                latency_ms_per_capability={"reranker": lat},
            )
        c.record_recall_query(
            backend_used={"reranker": "haiku"},
            latency_ms_per_capability={},
            event_subtype="error",
        )
        summary = MetricsAggregator(c).backend_summary()
        key = "reranker::haiku"
        assert summary["per_backend"][key]["count"] == 3
        assert summary["per_backend"][key]["measured_count"] == 2
        # avg over measured samples only
        assert summary["per_backend"][key]["avg_latency_ms"] == 10.0


# ---------------------------------------------------------------------------
# _percentile helper
# ---------------------------------------------------------------------------

class TestPercentileHelper:
    def test_empty_returns_zero(self):
        assert _percentile([], 0.5) == 0.0

    def test_p0_is_min(self):
        assert _percentile([3.0, 1.0, 2.0], 0.0) == 1.0

    def test_p100_is_max(self):
        assert _percentile([3.0, 1.0, 2.0], 1.0) == 3.0

    def test_p50_is_median_ish(self):
        # Nearest-rank p50 of [1,2,3,4,5] = element at ceil(0.5*5)-1 = 2 → value 3
        assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.50) == 3.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_one(path) -> dict:
    """Read the first JSONL line from `path` as a dict."""
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert lines, f"{path} is empty"
    return json.loads(lines[0])
