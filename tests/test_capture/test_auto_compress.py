"""Tests for S-77 auto-compress cadence — idle_sessions() and env-var plumbing."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.capture.compressor import idle_sessions, SessionCompressor


# ---------------------------------------------------------------------------
# idle_sessions — core idle detection
# ---------------------------------------------------------------------------

class TestIdleSessions:
    def test_empty_dir_returns_empty(self, tmp_path):
        assert idle_sessions(tmp_path, min_age_hours=1.0) == []

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        assert idle_sessions(tmp_path / "ghost", min_age_hours=1.0) == []

    def test_zero_or_negative_age_returns_empty(self, tmp_path):
        (tmp_path / "a.tmp").write_text("x")
        assert idle_sessions(tmp_path, min_age_hours=0.0) == []
        assert idle_sessions(tmp_path, min_age_hours=-1.0) == []

    def test_fresh_file_not_returned(self, tmp_path):
        p = tmp_path / "session.tmp"
        p.write_text("content")
        # File was just created — age is seconds, threshold is 2 hours
        result = idle_sessions(tmp_path, min_age_hours=2.0)
        assert result == []

    def test_old_file_returned(self, tmp_path):
        p = tmp_path / "old.tmp"
        p.write_text("content")
        # Backdate mtime to 3 hours ago
        old_ts = time.time() - 3 * 3600
        os.utime(p, (old_ts, old_ts))
        result = idle_sessions(tmp_path, min_age_hours=2.0)
        assert result == [p]

    def test_only_old_files_returned(self, tmp_path):
        old = tmp_path / "old.tmp"
        old.write_text("old content")
        fresh = tmp_path / "fresh.tmp"
        fresh.write_text("fresh content")

        old_ts = time.time() - 5 * 3600
        os.utime(old, (old_ts, old_ts))
        # fresh has current mtime

        result = idle_sessions(tmp_path, min_age_hours=2.0)
        assert result == [old]
        assert fresh not in result

    def test_non_tmp_files_excluded(self, tmp_path):
        md_file = tmp_path / "session.md"
        md_file.write_text("content")
        old_ts = time.time() - 5 * 3600
        os.utime(md_file, (old_ts, old_ts))

        result = idle_sessions(tmp_path, min_age_hours=1.0)
        assert result == []

    def test_sorted_oldest_first(self, tmp_path):
        files = []
        for i in range(3):
            p = tmp_path / f"session_{i}.tmp"
            p.write_text("x")
            # older = larger i (more hours ago)
            ts = time.time() - (i + 2) * 3600
            os.utime(p, (ts, ts))
            files.append((ts, p))

        files.sort()
        result = idle_sessions(tmp_path, min_age_hours=1.0)
        assert result == [p for _, p in files]

    def test_now_override(self, tmp_path):
        p = tmp_path / "session.tmp"
        p.write_text("content")
        # File has current mtime. Provide a 'now' far in the future.
        future_now = datetime.fromtimestamp(time.time() + 10 * 3600, tz=timezone.utc)
        result = idle_sessions(tmp_path, min_age_hours=2.0, now=future_now)
        assert result == [p]


# ---------------------------------------------------------------------------
# Config: auto_compress_hours env-var plumbing
# ---------------------------------------------------------------------------

class TestAutoCompressConfig:
    def test_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_AUTO_COMPRESS_HOURS", raising=False)
        from depthfusion.core.config import DepthFusionConfig
        cfg = DepthFusionConfig.from_env()
        assert cfg.auto_compress_hours is None

    def test_set_returns_float(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AUTO_COMPRESS_HOURS", "4.5")
        from depthfusion.core.config import DepthFusionConfig
        cfg = DepthFusionConfig.from_env()
        assert cfg.auto_compress_hours == pytest.approx(4.5)

    def test_invalid_value_returns_none(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AUTO_COMPRESS_HOURS", "not_a_number")
        from depthfusion.core.config import DepthFusionConfig
        cfg = DepthFusionConfig.from_env()
        assert cfg.auto_compress_hours is None

    def test_default_is_none(self):
        from depthfusion.core.config import DepthFusionConfig
        cfg = DepthFusionConfig()
        assert cfg.auto_compress_hours is None


# ---------------------------------------------------------------------------
# auto-compress script — integration
# ---------------------------------------------------------------------------

class TestAutoCompressScript:
    def _run(self, argv=None):
        import importlib.util
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "auto_compress",
            _Path(__file__).parents[2] / "scripts" / "auto-compress.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main(argv or [])

    def test_exits_zero_when_disabled(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_AUTO_COMPRESS_HOURS", raising=False)
        assert self._run() == 0

    def test_dry_run_lists_idle_files(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEPTHFUSION_AUTO_COMPRESS_HOURS", "1.0")
        monkeypatch.setattr(
            "depthfusion.capture.compressor.Path.home", lambda: tmp_path
        )
        sessions_dir = tmp_path / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        old = sessions_dir / "idle.tmp"
        old.write_text("session content")
        old_ts = time.time() - 2 * 3600
        os.utime(old, (old_ts, old_ts))

        exit_code = self._run(["--dry-run"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "idle.tmp" in captured.out

    def test_nothing_to_compress_exits_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEPTHFUSION_AUTO_COMPRESS_HOURS", "2.0")
        monkeypatch.setattr(
            "depthfusion.capture.compressor.Path.home", lambda: tmp_path
        )
        sessions_dir = tmp_path / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        # No files older than 2h

        exit_code = self._run()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "nothing idle" in out
