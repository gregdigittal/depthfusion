# tests/test_metrics/test_startup_check.py
"""S-79 AC-3 / T-267 — startup self-check and system.startup event.

AC-5 coverage:
  - system.startup record is written to the legacy metrics stream on server init
  - metrics directory writability is validated at startup (not deferred)
  - warning is logged (not raised) when the metrics directory is unwritable
"""
from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest

from depthfusion.mcp.server import _emit_startup_event


class TestEmitStartupEvent:
    def test_writes_system_startup_to_legacy_stream(self, tmp_path: Path) -> None:
        _emit_startup_event(tools_enabled=12, metrics_dir=tmp_path)

        files = list(tmp_path.glob("*.jsonl"))
        # Must write to the simple stream, not capture/recall/gates
        assert len(files) == 1
        assert "-capture" not in files[0].name
        assert "-recall" not in files[0].name
        assert "-gates" not in files[0].name

        record = json.loads(files[0].read_text().strip())
        assert record["metric"] == "system.startup"
        assert record["value"] == 1.0
        assert record["labels"]["tools_enabled"] == 12

    def test_startup_record_includes_server_version(self, tmp_path: Path) -> None:
        _emit_startup_event(tools_enabled=5, metrics_dir=tmp_path)

        record = json.loads(next(tmp_path.glob("*.jsonl")).read_text().strip())
        assert "server_version" in record["labels"]
        assert isinstance(record["labels"]["server_version"], str)
        assert len(record["labels"]["server_version"]) > 0

    def test_unwritable_metrics_dir_logs_warning_not_raises(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        metrics_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write

        try:
            with caplog.at_level(logging.WARNING, logger="depthfusion.mcp.server"):
                _emit_startup_event(tools_enabled=3, metrics_dir=metrics_dir)
        finally:
            metrics_dir.chmod(stat.S_IRWXU)  # restore so tmp_path cleanup works

        assert any("system.startup" in r.message for r in caplog.records)
