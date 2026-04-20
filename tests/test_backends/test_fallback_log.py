# tests/test_backends/test_fallback_log.py
"""T-123: backend fallback observability.

Tests that `_emit_fallback_event` and the factory's NullBackend fallback
path emit a JSONL record to MetricsCollector when
DEPTHFUSION_BACKEND_FALLBACK_LOG is enabled (the default), and suppress
it when the env var is disabled.

Covers:
  - Record is written when haiku is unhealthy (no API key)
  - Record contains expected fields (metric name, labels)
  - DEPTHFUSION_BACKEND_FALLBACK_LOG=false / 0 suppresses the record
  - MetricsCollector errors are swallowed (observability never breaks serving)
  - Healthy haiku (key present) produces no fallback record
  - Gemma unhealthy path also emits a record
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from depthfusion.backends.factory import _emit_fallback_event, get_backend

# ── _emit_fallback_event unit tests ──────────────────────────────────────


def test_emit_writes_record_to_collector(tmp_path, monkeypatch):
    """Default (no env var set) → record written to metrics dir."""
    monkeypatch.delenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", raising=False)
    monkeypatch.setenv("DEPTHFUSION_METRICS_DIR", str(tmp_path))

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        _emit_fallback_event(
            requested="haiku",
            capability="reranker",
            reason="no API key",
        )
    mock_record.assert_called_once()
    call_args = mock_record.call_args
    assert call_args.args[0] == "backend.fallback"
    assert call_args.args[1] == 1.0
    labels = call_args.kwargs.get("labels") or call_args.args[2]
    assert labels["requested"] == "haiku"
    assert labels["actual"] == "null"
    assert labels["capability"] == "reranker"


def test_emit_suppressed_when_flag_false(monkeypatch):
    """DEPTHFUSION_BACKEND_FALLBACK_LOG=false → no record written."""
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "false")

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        _emit_fallback_event(
            requested="haiku",
            capability="reranker",
            reason="no API key",
        )
    mock_record.assert_not_called()


@pytest.mark.parametrize("flag_value", ["0", "no", "off", "False", "FALSE"])
def test_emit_suppressed_for_all_false_spellings(flag_value, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", flag_value)

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        _emit_fallback_event(requested="gemma", capability="extractor", reason="empty URL")
    mock_record.assert_not_called()


def test_emit_enabled_when_flag_is_true(monkeypatch):
    """Explicit DEPTHFUSION_BACKEND_FALLBACK_LOG=true → record written."""
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true")

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        _emit_fallback_event(requested="haiku", capability="linker", reason="no SDK")
    mock_record.assert_called_once()


def test_emit_swallows_collector_error(monkeypatch):
    """A MetricsCollector write failure must not propagate — observability
    must never break serving (T-123 contract).
    """
    monkeypatch.delenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", raising=False)

    with patch("depthfusion.metrics.collector.MetricsCollector.record", side_effect=OSError("disk full")):
        # Must not raise
        _emit_fallback_event(requested="haiku", capability="reranker", reason="no key")


def test_emit_record_has_all_required_labels(monkeypatch):
    """Labels must include: requested, actual, capability, reason."""
    monkeypatch.delenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", raising=False)

    captured_labels: dict = {}

    def capture_record(metric_name, value, labels=None):
        captured_labels.update(labels or {})

    with patch("depthfusion.metrics.collector.MetricsCollector.record", side_effect=capture_record):
        _emit_fallback_event(
            requested="gemma",
            capability="summariser",
            reason="missing model config",
        )

    assert captured_labels["requested"] == "gemma"
    assert captured_labels["actual"] == "null"
    assert captured_labels["capability"] == "summariser"
    assert "reason" in captured_labels


# ── Factory integration: fallback events through get_backend() ─────────────


def test_factory_haiku_fallback_emits_record(monkeypatch):
    """haiku requested, no DEPTHFUSION_API_KEY → NullBackend + fallback event."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true")
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        from depthfusion.backends.null import NullBackend
        backend = get_backend("reranker")

    assert isinstance(backend, NullBackend)
    mock_record.assert_called_once()
    labels = mock_record.call_args.kwargs.get("labels") or mock_record.call_args.args[2]
    assert labels["requested"] == "haiku"
    assert labels["actual"] == "null"
    assert labels["capability"] == "reranker"


def test_factory_healthy_haiku_does_not_emit_fallback(monkeypatch):
    """Healthy haiku (API key present) → no fallback event."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true")
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-test-key")
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        get_backend("reranker")

    mock_record.assert_not_called()


def test_factory_fallback_suppressed_when_flag_disabled(monkeypatch):
    """Factory fallback with DEPTHFUSION_BACKEND_FALLBACK_LOG=false → no record."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "false")
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        get_backend("reranker")

    mock_record.assert_not_called()


def test_factory_gemma_fallback_emits_record(monkeypatch):
    """Gemma with empty URL → NullBackend + fallback event."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true")
    monkeypatch.setenv("DEPTHFUSION_GEMMA_URL", "")
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        from depthfusion.backends.null import NullBackend
        backend = get_backend("reranker")

    assert isinstance(backend, NullBackend)
    mock_record.assert_called_once()
    labels = mock_record.call_args.kwargs.get("labels") or mock_record.call_args.args[2]
    assert labels["requested"] == "gemma"


def test_null_backend_requested_directly_no_fallback_event(monkeypatch):
    """Explicitly requesting null does not count as a fallback — it's intentional."""
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "null")
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true")

    with patch("depthfusion.metrics.collector.MetricsCollector.record") as mock_record:
        get_backend("reranker")

    mock_record.assert_not_called()


# ── Actual JSONL written to disk (end-to-end through MetricsCollector) ────


def test_fallback_event_persisted_to_jsonl(tmp_path, monkeypatch):
    """End-to-end: record written by real MetricsCollector ends up as valid JSONL."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true")
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)

    # Point MetricsCollector at tmp_path so we don't write to ~/.claude
    from depthfusion.metrics.collector import MetricsCollector
    real_init = MetricsCollector.__init__

    def patched_init(self, metrics_dir=None):
        real_init(self, metrics_dir=tmp_path)

    with patch.object(MetricsCollector, "__init__", patched_init):
        get_backend("reranker")

    # Exactly one JSONL file in tmp_path
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert len(jsonl_files) == 1, f"Expected one .jsonl file, got: {jsonl_files}"

    lines = jsonl_files[0].read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["metric"] == "backend.fallback"
    assert record["value"] == 1.0
    assert record["labels"]["requested"] == "haiku"
    assert record["labels"]["actual"] == "null"
    assert "timestamp" in record
