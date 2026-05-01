"""Tests for S-73 high-importance event hook (capture/event_hook.py)."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from depthfusion.capture.event_hook import emit_if_high_importance
from depthfusion.core.types import ContextItem


def _make_item(**kwargs) -> ContextItem:
    defaults = dict(
        item_id="test-id",
        content="a discovery",
        source_agent="test-agent",
        tags=[],
        priority="normal",
        metadata={},
    )
    defaults.update(kwargs)
    return ContextItem(**defaults)


class TestThresholdTrigger:
    def test_emits_when_importance_at_threshold(self, tmp_path):
        item = _make_item(importance=0.8)
        log = str(tmp_path / "events.jsonl")
        result = emit_if_high_importance(item, event_log=log, threshold=0.8)
        assert result is True

    def test_emits_when_importance_above_threshold(self, tmp_path):
        item = _make_item(importance=0.95)
        log = str(tmp_path / "events.jsonl")
        assert emit_if_high_importance(item, event_log=log, threshold=0.8) is True

    def test_does_not_emit_below_threshold(self, tmp_path):
        item = _make_item(importance=0.79)
        log = str(tmp_path / "events.jsonl")
        result = emit_if_high_importance(item, event_log=log, threshold=0.8)
        assert result is False

    def test_no_file_written_below_threshold(self, tmp_path):
        item = _make_item(importance=0.5)
        log_base = tmp_path / "events.jsonl"
        emit_if_high_importance(item, event_log=str(log_base), threshold=0.8)
        # no dated file should exist
        assert list(tmp_path.glob("events-*.jsonl")) == []


class TestEventSchema:
    def _read_event(self, tmp_path) -> dict:
        files = list(tmp_path.glob("events-*.jsonl"))
        assert len(files) == 1, f"expected 1 dated log file, got {files}"
        lines = files[0].read_text().strip().splitlines()
        assert len(lines) == 1
        return json.loads(lines[0])

    def test_all_required_fields_present(self, tmp_path):
        item = _make_item(importance=0.9, salience=2.0)
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        event = self._read_event(tmp_path)
        for field in ("timestamp", "event", "project", "file_path", "importance", "salience", "summary"):
            assert field in event, f"missing field: {field}"

    def test_event_type_is_correct(self, tmp_path):
        item = _make_item(importance=0.9)
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        assert self._read_event(tmp_path)["event"] == "high_importance_discovery"

    def test_importance_and_salience_values(self, tmp_path):
        item = _make_item(importance=0.9, salience=3.0)
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        event = self._read_event(tmp_path)
        assert event["importance"] == pytest.approx(0.9)
        assert event["salience"] == pytest.approx(3.0)

    def test_project_falls_back_to_source_agent(self, tmp_path):
        item = _make_item(importance=0.9, source_agent="my-agent", metadata={})
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        assert self._read_event(tmp_path)["project"] == "my-agent"

    def test_project_from_metadata(self, tmp_path):
        item = _make_item(importance=0.9, metadata={"project": "myproject"})
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        assert self._read_event(tmp_path)["project"] == "myproject"

    def test_file_path_from_metadata(self, tmp_path):
        item = _make_item(importance=0.9, metadata={"file_path": "/some/file.md"})
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        assert self._read_event(tmp_path)["file_path"] == "/some/file.md"

    def test_summary_truncated_from_content(self, tmp_path):
        long_content = "x" * 1000
        item = _make_item(importance=0.9, content=long_content)
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        assert len(self._read_event(tmp_path)["summary"]) <= 500

    def test_summary_from_metadata(self, tmp_path):
        item = _make_item(importance=0.9, content="long content", metadata={"summary": "short"})
        emit_if_high_importance(item, event_log=str(tmp_path / "events.jsonl"), threshold=0.8)
        assert self._read_event(tmp_path)["summary"] == "short"

    def test_daily_rotation_creates_dated_file(self, tmp_path):
        item = _make_item(importance=0.9)
        base = tmp_path / "events.jsonl"
        emit_if_high_importance(item, event_log=str(base), threshold=0.8)
        dated_files = list(tmp_path.glob("events-????-??-??.jsonl"))
        assert len(dated_files) == 1

    def test_multiple_events_appended_to_same_file(self, tmp_path):
        log = str(tmp_path / "events.jsonl")
        for _ in range(3):
            emit_if_high_importance(_make_item(importance=0.9), event_log=log, threshold=0.8)
        files = list(tmp_path.glob("events-*.jsonl"))
        lines = files[0].read_text().strip().splitlines()
        assert len(lines) == 3


class TestEnvVarOverride:
    def test_custom_threshold_via_argument(self, tmp_path):
        item = _make_item(importance=0.6)
        log = str(tmp_path / "events.jsonl")
        # threshold lowered to 0.5 — item should trigger
        assert emit_if_high_importance(item, event_log=log, threshold=0.5) is True

    def test_custom_path_via_argument(self, tmp_path):
        custom = tmp_path / "custom" / "my-events.jsonl"
        item = _make_item(importance=0.9)
        emit_if_high_importance(item, event_log=str(custom), threshold=0.8)
        dated = list((tmp_path / "custom").glob("my-events-*.jsonl"))
        assert len(dated) == 1


class TestErrorHandling:
    def test_unwritable_path_returns_false(self):
        item = _make_item(importance=0.9)
        result = emit_if_high_importance(
            item,
            event_log="/proc/no_such_dir/events.jsonl",
            threshold=0.8,
        )
        assert result is False
