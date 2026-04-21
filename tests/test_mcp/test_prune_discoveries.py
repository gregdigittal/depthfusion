# tests/test_mcp/test_prune_discoveries.py
"""Discovery pruner tests — S-55 / T-171 / TG-14.

AC-3: ≥ 3 new tests. Covers:
  - identify_candidates: age_exceeded + superseded heuristics
  - prune_discoveries: confirm=False no-op, confirm=True moves to archive
  - MCP tool: confirm=False returns candidates, confirm=True moves
  - Safety: never deletes, archive collision handled, errors isolated
  - Env var: DEPTHFUSION_PRUNE_AGE_DAYS override
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from depthfusion.capture.pruner import (
    PruneCandidate,
    _read_age_days,
    identify_candidates,
    prune_discoveries,
)


def _make_file(
    parent: Path, name: str, content: str = "---\nproject: test\n---\n\nbody\n",
    *, age_days: float = 0.0,
) -> Path:
    """Create a discovery file with an mtime `age_days` in the past."""
    path = parent / name
    path.write_text(content, encoding="utf-8")
    if age_days != 0.0:
        ts = time.time() - (age_days * 86400.0)
        os.utime(path, (ts, ts))
    return path


# ---------------------------------------------------------------------------
# _read_age_days
# ---------------------------------------------------------------------------

class TestReadAgeDays:
    def test_default_is_90(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_PRUNE_AGE_DAYS", raising=False)
        assert _read_age_days() == 90

    def test_env_override_positive(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PRUNE_AGE_DAYS", "30")
        assert _read_age_days() == 30

    def test_env_malformed_falls_back(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PRUNE_AGE_DAYS", "not-a-number")
        assert _read_age_days() == 90

    def test_env_non_positive_falls_back(self, monkeypatch):
        """Zero or negative values are operator error → default."""
        monkeypatch.setenv("DEPTHFUSION_PRUNE_AGE_DAYS", "0")
        assert _read_age_days() == 90
        monkeypatch.setenv("DEPTHFUSION_PRUNE_AGE_DAYS", "-5")
        assert _read_age_days() == 90


# ---------------------------------------------------------------------------
# identify_candidates
# ---------------------------------------------------------------------------

class TestIdentifyCandidates:
    def test_missing_directory_returns_empty(self, tmp_path):
        assert identify_candidates(tmp_path / "nonexistent") == []

    def test_empty_directory_returns_empty(self, tmp_path):
        assert identify_candidates(tmp_path) == []

    def test_fresh_files_not_flagged(self, tmp_path):
        _make_file(tmp_path, "fresh.md", age_days=1.0)
        assert identify_candidates(tmp_path, age_days=90) == []

    def test_age_exceeded_flagged(self, tmp_path):
        _make_file(tmp_path, "fresh.md", age_days=1.0)
        _make_file(tmp_path, "stale.md", age_days=180.0)
        candidates = identify_candidates(tmp_path, age_days=90)
        assert len(candidates) == 1
        assert candidates[0].path.name == "stale.md"
        assert candidates[0].reason == "age_exceeded"
        assert candidates[0].age_days > 90

    def test_superseded_always_flagged(self, tmp_path):
        """Files with `.superseded` suffix are ALWAYS candidates, regardless
        of age — dedup (CM-2) already decided they're stale.
        """
        _make_file(tmp_path, "recent-dup.md.superseded", age_days=0.5)
        candidates = identify_candidates(tmp_path, age_days=90)
        assert len(candidates) == 1
        assert candidates[0].reason == "superseded"

    def test_deterministic_ordering(self, tmp_path):
        """Repeated calls produce identically-ordered output so operators
        can diff prune-candidate reports across runs.
        """
        for name in ("c.md", "a.md", "b.md"):
            _make_file(tmp_path, name, age_days=180.0)
        run1 = [c.path.name for c in identify_candidates(tmp_path, age_days=90)]
        run2 = [c.path.name for c in identify_candidates(tmp_path, age_days=90)]
        assert run1 == run2
        assert run1 == ["a.md", "b.md", "c.md"]

    def test_directories_ignored(self, tmp_path):
        """Subdirectories (including .archive/) are not treated as candidates."""
        (tmp_path / ".archive").mkdir()
        _make_file(tmp_path / ".archive", "old.md", age_days=200.0)
        # File outside the subdir
        _make_file(tmp_path, "stale.md", age_days=200.0)
        candidates = identify_candidates(tmp_path, age_days=90)
        names = [c.path.name for c in candidates]
        assert names == ["stale.md"]

    def test_env_var_used_when_age_days_not_passed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PRUNE_AGE_DAYS", "5")
        _make_file(tmp_path, "ten-day.md", age_days=10.0)
        # With env=5 days, 10-day file is a candidate
        cands = identify_candidates(tmp_path)
        assert len(cands) == 1

    def test_hidden_dot_files_skipped(self, tmp_path):
        """Review-gate regression: .DS_Store, .gitkeep, editor swap files
        (everything whose basename starts with `.`) must be filtered out.
        `.superseded` discoveries always have the suffix APPENDED, not
        prefixed — they pass through this filter.
        """
        # Hidden files — should be skipped
        _make_file(tmp_path, ".DS_Store", age_days=365.0)
        _make_file(tmp_path, ".gitkeep", age_days=365.0)
        # Legitimate superseded discovery — should be flagged
        _make_file(tmp_path, "old-dup.md.superseded", age_days=10.0)
        # Legitimate aged discovery — should be flagged
        _make_file(tmp_path, "ancient.md", age_days=200.0)

        cands = identify_candidates(tmp_path, age_days=90)
        names = sorted(c.path.name for c in cands)
        assert names == ["ancient.md", "old-dup.md.superseded"]
        # Neither hidden file appears as a candidate
        assert not any(c.path.name.startswith(".") for c in cands)


# ---------------------------------------------------------------------------
# prune_discoveries
# ---------------------------------------------------------------------------

class TestPruneDiscoveries:
    def test_confirm_false_is_no_op(self, tmp_path):
        """Default confirm=False never touches the filesystem."""
        path = _make_file(tmp_path, "stale.md", age_days=200.0)
        cands = [PruneCandidate(path=path, reason="age_exceeded", age_days=200.0)]
        result = prune_discoveries(cands, archive_dir=tmp_path / ".archive")
        # No files moved
        assert result == []
        # Source still exists
        assert path.exists()
        # No archive dir created (or if created, source still in place)
        assert not (tmp_path / ".archive" / "stale.md").exists()

    def test_confirm_true_moves_to_archive(self, tmp_path):
        path = _make_file(tmp_path, "stale.md", age_days=200.0)
        archive = tmp_path / ".archive"
        cands = [PruneCandidate(path=path, reason="age_exceeded", age_days=200.0)]
        moved = prune_discoveries(cands, archive_dir=archive, confirm=True)
        assert len(moved) == 1
        assert moved[0].name == "stale.md"
        assert moved[0].parent == archive
        # Source gone
        assert not path.exists()
        # Archived
        assert (archive / "stale.md").exists()

    def test_collision_gets_timestamp_suffix(self, tmp_path):
        """If the archive already has a file with the same name (e.g. from
        a prior prune run), the new file gets a timestamp-stemmed name
        instead of overwriting.
        """
        archive = tmp_path / ".archive"
        archive.mkdir()
        # Pre-existing archived file with the same name
        (archive / "stale.md").write_text("PREVIOUS ARCHIVE", encoding="utf-8")
        # New file being pruned
        path = _make_file(tmp_path, "stale.md", content="NEW STALE", age_days=200.0)
        cands = [PruneCandidate(path=path, reason="age_exceeded", age_days=200.0)]
        moved = prune_discoveries(cands, archive_dir=archive, confirm=True)
        assert len(moved) == 1
        # Previous archive preserved intact
        assert (archive / "stale.md").read_text() == "PREVIOUS ARCHIVE"
        # New file got a suffix
        assert moved[0].name != "stale.md"
        assert moved[0].name.startswith("stale.")
        assert moved[0].name.endswith(".md")
        assert moved[0].read_text() == "NEW STALE"

    def test_missing_source_silently_skipped(self, tmp_path):
        """If the candidate's path was removed between identification and
        prune (race), the prune operation skips that entry rather than
        crashing the batch.
        """
        archive = tmp_path / ".archive"
        cands = [PruneCandidate(
            path=tmp_path / "ghost.md", reason="age_exceeded", age_days=500.0,
        )]
        moved = prune_discoveries(cands, archive_dir=archive, confirm=True)
        assert moved == []

    def test_never_deletes_only_moves(self, tmp_path):
        """Safety contract: archive must always contain the file data;
        source must be gone but data preserved.
        """
        path = _make_file(tmp_path, "stale.md", content="CRITICAL CONTENT",
                          age_days=200.0)
        archive = tmp_path / ".archive"
        cands = [PruneCandidate(path=path, reason="age_exceeded", age_days=200.0)]
        moved = prune_discoveries(cands, archive_dir=archive, confirm=True)
        # Source deleted from its old location
        assert not path.exists()
        # Content preserved in archive
        assert moved[0].read_text() == "CRITICAL CONTENT"


# ---------------------------------------------------------------------------
# MCP tool: _tool_prune_discoveries
# ---------------------------------------------------------------------------

class TestMcpPruneDiscoveries:
    def test_confirm_false_returns_candidates_without_moving(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)
        _make_file(disc, "stale.md", age_days=200.0)

        from depthfusion.mcp.server import _tool_prune_discoveries
        result_json = _tool_prune_discoveries({"age_days": 90})
        result = json.loads(result_json)
        assert result["ok"] is True
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["reason"] == "age_exceeded"
        assert result["moved"] == []
        # Stale file still in place
        assert (disc / "stale.md").exists()
        # No archive created
        assert not (disc / ".archive").exists()

    def test_confirm_true_moves_to_archive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)
        stale = _make_file(disc, "stale.md", age_days=200.0)

        from depthfusion.mcp.server import _tool_prune_discoveries
        result_json = _tool_prune_discoveries({"age_days": 90, "confirm": True})
        result = json.loads(result_json)
        assert result["ok"] is True
        assert len(result["moved"]) == 1
        assert not stale.exists()
        assert (disc / ".archive" / "stale.md").exists()

    def test_invalid_age_days_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / ".claude" / "shared" / "discoveries").mkdir(parents=True)

        from depthfusion.mcp.server import _tool_prune_discoveries
        result = json.loads(_tool_prune_discoveries({"age_days": -1}))
        assert result["ok"] is False
        assert "positive" in result["error"]

        result = json.loads(_tool_prune_discoveries({"age_days": "nonsense"}))
        assert result["ok"] is False

    def test_tool_registered_in_tools_dict(self):
        """Tool surface: schema + dispatch table must both reference the tool."""
        from depthfusion.mcp.server import _TOOL_FLAGS, TOOLS
        assert "depthfusion_prune_discoveries" in TOOLS
        assert _TOOL_FLAGS["depthfusion_prune_discoveries"] is None  # always enabled
        desc = TOOLS["depthfusion_prune_discoveries"]
        assert "confirm" in desc
        assert ".archive/" in desc

    def test_tool_appears_in_enabled_tools(self):
        """Always-enabled tools show up in get_enabled_tools regardless of config."""
        from depthfusion.mcp.server import get_enabled_tools

        class _Cfg:
            pass

        enabled = get_enabled_tools(_Cfg())
        assert "depthfusion_prune_discoveries" in enabled
