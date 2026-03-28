"""Tests for MetricsCollector and MetricsAggregator."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from depthfusion.metrics.aggregator import MetricsAggregator
from depthfusion.metrics.collector import MetricsCollector


@pytest.fixture
def collector(tmp_path: Path) -> MetricsCollector:
    return MetricsCollector(metrics_dir=tmp_path / "metrics")


@pytest.fixture
def aggregator(collector: MetricsCollector) -> MetricsAggregator:
    return MetricsAggregator(collector=collector)


def test_record_creates_daily_file(collector: MetricsCollector):
    collector.record("test_metric", 1.0)
    assert collector.today_path().exists()


def test_record_appends_valid_json(collector: MetricsCollector):
    collector.record("rlm_cost", 0.05, labels={"strategy": "peek"})
    lines = collector.today_path().read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["metric"] == "rlm_cost"
    assert entry["value"] == 0.05
    assert entry["labels"] == {"strategy": "peek"}
    assert "timestamp" in entry


def test_record_multiple_appends_all(collector: MetricsCollector):
    collector.record("metric_a", 1.0)
    collector.record("metric_b", 2.0)
    collector.record("metric_a", 3.0)
    lines = collector.today_path().read_text().strip().splitlines()
    assert len(lines) == 3


def test_today_path_returns_correct_date(collector: MetricsCollector):
    expected_date = date.today().isoformat()
    assert expected_date in collector.today_path().name


def test_daily_summary_empty_for_no_file(aggregator: MetricsAggregator):
    result = aggregator.daily_summary(target_date=date(2000, 1, 1))
    assert result == {}


def test_daily_summary_aggregates_correctly(collector: MetricsCollector, aggregator: MetricsAggregator):
    collector.record("rlm_cost", 0.10)
    collector.record("rlm_cost", 0.20)
    collector.record("rlm_cost", 0.30)
    summary = aggregator.daily_summary()
    assert "rlm_cost" in summary
    stats = summary["rlm_cost"]
    assert stats["count"] == 3
    assert abs(stats["sum"] - 0.60) < 1e-9
    assert abs(stats["avg"] - 0.20) < 1e-9
    assert stats["min"] == 0.10
    assert stats["max"] == 0.30


def test_daily_summary_multiple_metrics(collector: MetricsCollector, aggregator: MetricsAggregator):
    collector.record("metric_a", 5.0)
    collector.record("metric_b", 10.0)
    collector.record("metric_a", 3.0)
    summary = aggregator.daily_summary()
    assert "metric_a" in summary
    assert "metric_b" in summary
    assert summary["metric_a"]["count"] == 2
    assert summary["metric_b"]["count"] == 1


def test_format_for_digest_returns_non_empty(collector: MetricsCollector, aggregator: MetricsAggregator):
    collector.record("rlm_cost", 0.05)
    summary = aggregator.daily_summary()
    digest = aggregator.format_for_digest(summary)
    assert len(digest) > 0
    assert "DepthFusion Metrics" in digest


def test_format_for_digest_contains_metric_name(collector: MetricsCollector, aggregator: MetricsAggregator):
    collector.record("token_count", 1500.0)
    summary = aggregator.daily_summary()
    digest = aggregator.format_for_digest(summary)
    assert "token_count" in digest


def test_format_for_digest_empty_summary(aggregator: MetricsAggregator):
    digest = aggregator.format_for_digest({})
    assert "No metrics" in digest


def test_record_no_labels_defaults_to_empty_dict(collector: MetricsCollector):
    collector.record("simple_metric", 42.0)
    entry = json.loads(collector.today_path().read_text().strip())
    assert entry["labels"] == {}
