"""Tests for S-69 — pin discoveries to exempt them from age-based pruning.

AC-4 requires ≥ 4 tests covering:
  - pin sets pinned: true in frontmatter
  - unpin sets pinned: false in frontmatter
  - pinned file skipped during identify_candidates (prune bypass)
  - missing-file edge case returns structured error (no raise)

Additional tests cover:
  - idempotency (pin twice, unpin twice)
  - backward compat (missing `pinned` key defaults to False)
  - MCP tool surface (registration + dispatch)
  - _splice_pin_frontmatter: no-frontmatter creation path
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from depthfusion.capture.pruner import _is_pinned, identify_candidates
from depthfusion.core.file_locking import _splice_pin_frontmatter
from depthfusion.mcp.server import _TOOL_FLAGS, TOOLS, _tool_pin_discovery, get_enabled_tools

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_discovery(
    parent: Path,
    name: str = "disc.md",
    content: str = "---\nproject: test\nimportance: 0.5000\nsalience: 1.0000\n---\n\nbody\n",
    *,
    age_days: float = 0.0,
) -> Path:
    """Write a discovery file and optionally backdate its mtime."""
    path = parent / name
    path.write_text(content, encoding="utf-8")
    if age_days != 0.0:
        ts = time.time() - (age_days * 86400.0)
        os.utime(path, (ts, ts))
    return path


# ---------------------------------------------------------------------------
# _splice_pin_frontmatter unit tests
# ---------------------------------------------------------------------------

class TestSplicePinFrontmatter:
    def test_adds_pinned_true_to_existing_frontmatter(self):
        body = "---\nproject: x\n---\n\nbody\n"
        result = _splice_pin_frontmatter(body, True)
        assert "pinned: true" in result
        assert result.startswith("---\n")

    def test_adds_pinned_false_to_existing_frontmatter(self):
        body = "---\nproject: x\n---\n\nbody\n"
        result = _splice_pin_frontmatter(body, False)
        assert "pinned: false" in result

    def test_rewrites_existing_pinned_line(self):
        body = "---\nproject: x\npinned: false\n---\n\nbody\n"
        result = _splice_pin_frontmatter(body, True)
        assert "pinned: true" in result
        assert result.count("pinned:") == 1

    def test_creates_frontmatter_block_when_absent(self):
        body = "just plain body text, no frontmatter\n"
        result = _splice_pin_frontmatter(body, True)
        assert result.startswith("---\n")
        assert "pinned: true" in result
        assert "just plain body text" in result

    def test_preserves_other_frontmatter_fields(self):
        body = "---\nproject: myproj\nimportance: 0.8000\n---\n\nbody\n"
        result = _splice_pin_frontmatter(body, True)
        assert "project: myproj" in result
        assert "importance: 0.8000" in result
        assert "pinned: true" in result


# ---------------------------------------------------------------------------
# _is_pinned helper
# ---------------------------------------------------------------------------

class TestIsPinned:
    def test_returns_false_when_key_absent(self, tmp_path):
        path = _make_discovery(tmp_path, content="---\nproject: test\n---\n\nbody\n")
        assert _is_pinned(path) is False

    def test_returns_true_when_pinned_true(self, tmp_path):
        path = _make_discovery(
            tmp_path,
            content="---\nproject: test\npinned: true\n---\n\nbody\n",
        )
        assert _is_pinned(path) is True

    def test_returns_false_when_pinned_false(self, tmp_path):
        path = _make_discovery(
            tmp_path,
            content="---\nproject: test\npinned: false\n---\n\nbody\n",
        )
        assert _is_pinned(path) is False

    def test_returns_false_on_missing_file(self, tmp_path):
        assert _is_pinned(tmp_path / "ghost.md") is False


# ---------------------------------------------------------------------------
# AC-1: pin tool sets pinned: true (via MCP tool handler)
# ---------------------------------------------------------------------------

class TestPinToolSetsTrue:
    def test_pin_sets_pinned_true_in_frontmatter(self, tmp_path):
        path = _make_discovery(tmp_path)
        result = json.loads(_tool_pin_discovery({"filename": str(path), "pinned": True}))
        assert result == {"pinned": True, "filename": str(path)}
        assert "pinned: true" in path.read_text()

    def test_pin_default_pinned_true(self, tmp_path):
        """pinned defaults to True when omitted."""
        path = _make_discovery(tmp_path)
        result = json.loads(_tool_pin_discovery({"filename": str(path)}))
        assert result["pinned"] is True
        assert "pinned: true" in path.read_text()

    def test_pin_idempotent_calling_twice(self, tmp_path):
        path = _make_discovery(tmp_path)
        _tool_pin_discovery({"filename": str(path), "pinned": True})
        result = json.loads(_tool_pin_discovery({"filename": str(path), "pinned": True}))
        assert result["pinned"] is True
        assert path.read_text().count("pinned:") == 1


# ---------------------------------------------------------------------------
# AC-2: unpin tool sets pinned: false
# ---------------------------------------------------------------------------

class TestPinToolSetsFalse:
    def test_unpin_sets_pinned_false_in_frontmatter(self, tmp_path):
        # Start pinned
        path = _make_discovery(
            tmp_path,
            content="---\nproject: test\npinned: true\n---\n\nbody\n",
        )
        result = json.loads(_tool_pin_discovery({"filename": str(path), "pinned": False}))
        assert result == {"pinned": False, "filename": str(path)}
        assert "pinned: false" in path.read_text()
        assert path.read_text().count("pinned:") == 1

    def test_unpin_idempotent_on_already_unpinned(self, tmp_path):
        path = _make_discovery(tmp_path)  # no pinned key
        result = json.loads(_tool_pin_discovery({"filename": str(path), "pinned": False}))
        assert result["pinned"] is False
        assert path.read_text().count("pinned:") == 1


# ---------------------------------------------------------------------------
# AC-3: pinned file is skipped during prune
# ---------------------------------------------------------------------------

class TestPinnedFileSkippedDuringPrune:
    def test_pinned_stale_file_not_in_candidates(self, tmp_path):
        """A file with `pinned: true` must NOT appear in prune candidates even
        if it exceeds the age threshold.
        """
        pinned = _make_discovery(
            tmp_path,
            name="pinned-stale.md",
            content="---\nproject: test\npinned: true\n---\n\nbody\n",
            age_days=200.0,
        )
        unpinned = _make_discovery(
            tmp_path,
            name="unpinned-stale.md",
            age_days=200.0,
        )
        candidates = identify_candidates(tmp_path, age_days=90)
        names = [c.path.name for c in candidates]
        assert pinned.name not in names
        assert unpinned.name in names

    def test_pinned_superseded_file_also_skipped(self, tmp_path):
        """A pinned `.superseded` file must also be exempt (pinned wins)."""
        pinned_sup = _make_discovery(
            tmp_path,
            name="old-dup.md.superseded",
            content="---\nproject: test\npinned: true\n---\n\nbody\n",
            age_days=5.0,
        )
        candidates = identify_candidates(tmp_path, age_days=90)
        names = [c.path.name for c in candidates]
        assert pinned_sup.name not in names

    def test_unpinned_stale_still_candidate_after_unpin(self, tmp_path):
        """Unpinning a previously-pinned file restores it as a prune candidate."""
        path = _make_discovery(
            tmp_path,
            name="was-pinned.md",
            content="---\nproject: test\npinned: false\n---\n\nbody\n",
            age_days=200.0,
        )
        candidates = identify_candidates(tmp_path, age_days=90)
        names = [c.path.name for c in candidates]
        assert path.name in names


# ---------------------------------------------------------------------------
# AC-4: missing-file edge case
# ---------------------------------------------------------------------------

class TestMissingFileEdgeCase:
    def test_missing_file_returns_error_dict_not_raise(self, tmp_path):
        result = json.loads(
            _tool_pin_discovery({"filename": str(tmp_path / "ghost.md"), "pinned": True})
        )
        assert "error" in result
        assert result["error"] == "file not found"
        assert "ghost.md" in result["filename"]

    def test_empty_filename_returns_error(self):
        result = json.loads(_tool_pin_discovery({"filename": ""}))
        assert "error" in result

    def test_missing_filename_returns_error(self):
        result = json.loads(_tool_pin_discovery({}))
        assert "error" in result

    def test_non_bool_pinned_returns_error(self, tmp_path):
        path = _make_discovery(tmp_path)
        result = json.loads(_tool_pin_discovery({"filename": str(path), "pinned": "yes"}))
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool registration checks
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_tool_in_tools_dict(self):
        assert "depthfusion_pin_discovery" in TOOLS
        desc = TOOLS["depthfusion_pin_discovery"]
        assert "pin" in desc.lower()
        assert "filename" in desc

    def test_tool_always_enabled(self):
        assert _TOOL_FLAGS["depthfusion_pin_discovery"] is None

    def test_tool_appears_in_enabled_tools(self):
        class _Cfg:
            pass

        enabled = get_enabled_tools(_Cfg())
        assert "depthfusion_pin_discovery" in enabled
