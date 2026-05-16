# tests/test_capture/test_compressor.py
from unittest.mock import patch

from depthfusion.capture.compressor import SessionCompressor, _project_from_stem


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


# ---------------------------------------------------------------------------
# _project_from_stem helper
# ---------------------------------------------------------------------------

class TestProjectFromStem:
    def test_strips_date_and_session(self):
        assert _project_from_stem("2026-05-12-agent-ops-session") == "agent-ops"

    def test_strips_date_only(self):
        assert _project_from_stem("2026-05-12-depthfusion") == "depthfusion"

    def test_strips_session_only(self):
        # No date prefix — only strip -session suffix
        assert _project_from_stem("my-project-session") == "my-project"

    def test_plain_name(self):
        assert _project_from_stem("plain") == "plain"

    def test_empty_after_strip(self):
        # Edge: stem is only date + "-session"
        assert _project_from_stem("2026-05-12-session") == "unknown"


# ---------------------------------------------------------------------------
# Capture metric emission
# ---------------------------------------------------------------------------

def test_compress_emits_capture_event(tmp_path):
    session_file = tmp_path / "2026-05-12-agent-ops-session.tmp"
    session_file.write_text("→ Decision: use BM25\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()

    with patch.object(SessionCompressor, "is_available", return_value=False), \
         patch("depthfusion.capture.compressor.emit_capture_event") as mock_emit:
        c = SessionCompressor()
        output_path = c.compress(session_file, output_dir=discoveries_dir)

    assert output_path is not None
    mock_emit.assert_called_once()
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["capture_mechanism"] == "session_compressor"
    assert kwargs["write_success"] is True
    assert kwargs["entries_written"] == 1
    assert kwargs["file_path"] == str(output_path)
    assert kwargs["project"] == "agent-ops"


def test_compress_uses_explicit_project(tmp_path):
    session_file = tmp_path / "2026-05-12-agent-ops-session.tmp"
    session_file.write_text("→ Decision: use Redis\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()

    with patch.object(SessionCompressor, "is_available", return_value=False), \
         patch("depthfusion.capture.compressor.emit_capture_event") as mock_emit:
        c = SessionCompressor()
        c.compress(session_file, output_dir=discoveries_dir, project="my-project")

    mock_emit.assert_called_once()
    assert mock_emit.call_args.kwargs["project"] == "my-project"


def test_compress_no_emit_when_skipped(tmp_path):
    session_file = tmp_path / "2026-05-12-agent-ops-session.tmp"
    session_file.write_text("→ Decision: first\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()
    (discoveries_dir / "2026-05-12-agent-ops-session-autocapture.md").write_text("exists")

    with patch.object(SessionCompressor, "is_available", return_value=False), \
         patch("depthfusion.capture.compressor.emit_capture_event") as mock_emit:
        c = SessionCompressor()
        result = c.compress(session_file, output_dir=discoveries_dir)

    assert result is None
    mock_emit.assert_not_called()


def test_compress_emit_error_does_not_propagate(tmp_path):
    session_file = tmp_path / "2026-05-12-x-session.tmp"
    session_file.write_text("→ Decision: important\n", encoding="utf-8")
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()

    def boom(**kwargs):
        raise RuntimeError("metrics system down")

    with patch.object(SessionCompressor, "is_available", return_value=False), \
         patch("depthfusion.capture.compressor.emit_capture_event", side_effect=boom):
        c = SessionCompressor()
        output_path = c.compress(session_file, output_dir=discoveries_dir)

    # File should still be written despite metrics failure
    assert output_path is not None
    assert output_path.exists()
