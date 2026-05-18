"""Tests for S-110: PostToolUse ambient capture hook."""
from __future__ import annotations

import pytest

from depthfusion.capture.auto_learn import build_ambient_item
from depthfusion.hooks.post_tool_use import _extract_files, handle_post_tool_use
from depthfusion.router.bus import FileBus

# ---------------------------------------------------------------------------
# build_ambient_item (T-368)
# ---------------------------------------------------------------------------

class TestBuildAmbientItem:
    def test_returns_context_item_with_ambient_tags(self):
        item = build_ambient_item("Read", session_id="sess-abc")
        assert "ambient" in item.tags
        assert "tool-use" in item.tags
        assert "sess-abc" in item.tags

    def test_importance_is_low(self):
        item = build_ambient_item("Write", session_id="sess-1")
        assert item.importance == pytest.approx(0.3)

    def test_source_agent_is_ambient(self):
        item = build_ambient_item("Bash", session_id="sess-2")
        assert item.source_agent == "depthfusion-ambient"

    def test_files_read_stored(self):
        item = build_ambient_item(
            "Read",
            session_id="sess-3",
            files_read=["src/auth.py", "src/models.py"],
        )
        assert item.files_read == ["src/auth.py", "src/models.py"]

    def test_files_modified_stored(self):
        item = build_ambient_item(
            "Write",
            session_id="sess-4",
            files_modified=["src/output.py"],
        )
        assert item.files_modified == ["src/output.py"]

    def test_content_includes_tool_name(self):
        item = build_ambient_item("Edit", session_id="sess-5")
        assert "Edit" in item.content

    def test_item_id_unique_per_call(self):
        item1 = build_ambient_item("Read", session_id="sess-6")
        item2 = build_ambient_item("Read", session_id="sess-6")
        assert item1.item_id != item2.item_id

    def test_files_capped_at_20(self):
        many_files = [f"file_{i}.py" for i in range(30)]
        item = build_ambient_item("Read", session_id="sess-7", files_read=many_files)
        assert len(item.files_read) == 20

    def test_empty_files_default_to_empty_lists(self):
        item = build_ambient_item("Bash", session_id="sess-8")
        assert item.files_read == []
        assert item.files_modified == []


# ---------------------------------------------------------------------------
# _extract_files (T-367)
# ---------------------------------------------------------------------------

class TestExtractFiles:
    def test_read_tool_extracts_file_path(self):
        fr, fm = _extract_files("Read", {"file_path": "src/foo.py"})
        assert fr == ["src/foo.py"]
        assert fm == []

    def test_write_tool_extracts_file_modified(self):
        fr, fm = _extract_files("Write", {"file_path": "src/bar.py"})
        assert fr == []
        assert fm == ["src/bar.py"]

    def test_edit_tool_extracts_file_modified(self):
        fr, fm = _extract_files("Edit", {"file_path": "src/baz.py"})
        assert fr == []
        assert fm == ["src/baz.py"]

    def test_glob_extracts_pattern(self):
        fr, fm = _extract_files("Glob", {"pattern": "src/**/*.py"})
        assert fr == ["src/**/*.py"]
        assert fm == []

    def test_bash_extracts_nothing(self):
        fr, fm = _extract_files("Bash", {"command": "git status"})
        assert fr == []
        assert fm == []

    def test_unknown_tool_extracts_nothing(self):
        fr, fm = _extract_files("Agent", {"description": "some task"})
        assert fr == []
        assert fm == []


# ---------------------------------------------------------------------------
# handle_post_tool_use — feature flag and skip list (T-367)
# ---------------------------------------------------------------------------

class TestHandlePostToolUse:
    def test_disabled_by_flag_does_not_publish(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "false")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_post_tool_use({"tool_name": "Read", "session_id": "s1"})
        assert list(tmp_path.glob("*.jsonl")) == []

    def test_skip_listed_tool_not_published(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "Read,Bash")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_post_tool_use({"tool_name": "Read", "session_id": "s2"})
        results = FileBus(bus_dir=tmp_path).subscribe(["ambient"])
        assert results == []

    def test_tool_not_in_skip_list_is_published(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "Bash")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_post_tool_use({
            "tool_name": "Write",
            "session_id": "s3",
            "tool_input": {"file_path": "output.py"},
        })
        results = FileBus(bus_dir=tmp_path).subscribe(["ambient"])
        assert len(results) == 1
        assert "Write" in results[0].content

    def test_published_item_has_correct_tags(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_post_tool_use({"tool_name": "Edit", "session_id": "my-session"})
        results = FileBus(bus_dir=tmp_path).subscribe(["tool-use"])
        assert len(results) == 1
        assert "ambient" in results[0].tags
        assert "my-session" in results[0].tags

    def test_missing_tool_name_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_post_tool_use({"session_id": "s4"})  # no tool_name
        assert list(tmp_path.glob("*.jsonl")) == []

    def test_ambient_items_not_in_standard_recall_importance(self, tmp_path, monkeypatch):
        """AC-5: ambient items have importance=0.3, below recall threshold (0.8)."""
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_post_tool_use({"tool_name": "Read", "session_id": "s5"})
        results = FileBus(bus_dir=tmp_path).subscribe(["ambient"])
        assert len(results) == 1
        assert results[0].importance == pytest.approx(0.3)

    def test_no_exception_on_malformed_payload(self, tmp_path, monkeypatch):
        """AC-6: errors are non-fatal."""
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        # Should not raise
        handle_post_tool_use({"tool_name": None, "tool_input": "not a dict"})


# ---------------------------------------------------------------------------
# Integration: full hook → bus round-trip (T-371 AC-2)
# ---------------------------------------------------------------------------

class TestAmbientCaptureIntegration:
    def test_write_tool_publishes_files_modified(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_post_tool_use({
            "tool_name": "Write",
            "session_id": "integration-session",
            "tool_input": {"file_path": "src/new_feature.py"},
        })
        results = FileBus(bus_dir=tmp_path).subscribe(["ambient"])
        assert len(results) == 1
        item = results[0]
        assert item.files_modified == ["src/new_feature.py"]
        assert item.files_read == []
        assert item.importance == pytest.approx(0.3)
        assert "integration-session" in item.tags

    def test_multiple_tools_each_publish_one_item(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        for tool in ("Read", "Edit", "Bash"):
            handle_post_tool_use({"tool_name": tool, "session_id": "multi-sess"})
        results = FileBus(bus_dir=tmp_path).subscribe(["ambient"])
        assert len(results) == 3

    def test_skip_list_with_multiple_tools(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "Read,Bash,Glob")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        for tool in ("Read", "Bash", "Glob", "Write"):
            handle_post_tool_use({"tool_name": tool, "session_id": "skip-sess"})
        results = FileBus(bus_dir=tmp_path).subscribe(["ambient"])
        assert len(results) == 1  # only Write passes the skip filter
        assert "Write" in results[0].content
