# tests/test_capture/test_compressor.py
import pytest
from pathlib import Path
from unittest.mock import patch
from depthfusion.capture.compressor import SessionCompressor


def test_compressor_writes_discovery_file(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-test.tmp"
    session_file.write_text("# Goal: test\n→ Decision: use BM25\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()

    with patch.object(SessionCompressor, "is_available", return_value=False):
        c = SessionCompressor()
        output_path = c.compress(session_file, output_dir=discoveries_dir)

    assert output_path is not None
    assert output_path.exists()
    content = output_path.read_text()
    assert "BM25" in content or "Auto-Learned" in content


def test_compressor_skips_empty_file(tmp_path):
    session_file = tmp_path / "empty.tmp"
    session_file.write_text("  \n  ", encoding="utf-8")
    c = SessionCompressor()
    result = c.compress(session_file, output_dir=tmp_path)
    assert result is None


def test_compressor_output_filename_format(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-myfeature.tmp"
    session_file.write_text("# Goal\n→ Decision: important thing\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()
    with patch.object(SessionCompressor, "is_available", return_value=False):
        c = SessionCompressor()
        output_path = c.compress(session_file, output_dir=discoveries_dir)
    # Output should be in discoveries dir with .md extension
    assert output_path.parent == discoveries_dir
    assert output_path.suffix == ".md"


def test_compressor_does_not_overwrite_existing(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-test.tmp"
    session_file.write_text("→ Decision: first\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()
    existing = discoveries_dir / "2026-03-28-goal-test-autocapture.md"
    existing.write_text("# existing content", encoding="utf-8")

    with patch.object(SessionCompressor, "is_available", return_value=False):
        c = SessionCompressor()
        result = c.compress(session_file, output_dir=discoveries_dir)
    # Should not overwrite
    assert existing.read_text() == "# existing content"
    assert result is None  # skipped because already exists
