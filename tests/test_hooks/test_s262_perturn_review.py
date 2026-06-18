"""Tests for S-262: per-turn self-improvement loop in PostToolUse hook.

ACs:
  AC-1: Behind DEPTHFUSION_PERTURN_REVIEW env var, a forked tool-whitelisted
        review pass runs after a turn; cache-isolated.
  AC-2: Never edits a Tier-1 skill without the floor check; fail-closed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from depthfusion.hooks.post_tool_use import (
    floor_check_before_skill_edit,
    fork_review_pass,
    handle_post_tool_use,
)

# ---------------------------------------------------------------------------
# floor_check_before_skill_edit (AC-2)
# ---------------------------------------------------------------------------


class TestFloorCheckBeforeSkillEdit:
    def test_non_tier1_file_is_allowed(self, tmp_path):
        target = tmp_path / "src" / "mymodule.py"
        target.parent.mkdir(parents=True)
        target.write_text("# ordinary module\n")
        assert floor_check_before_skill_edit({"file": str(target)}) is True

    def test_file_with_tier1_marker_is_blocked(self, tmp_path):
        target = tmp_path / "some_skill.md"
        target.write_text("# My Skill\nrisk_tier: tier1\nsome content\n")
        assert floor_check_before_skill_edit({"file": str(target)}) is False

    def test_file_inside_tier1_dir_is_blocked(self, tmp_path, monkeypatch):
        # Patch _TIER1_DIR to a temp directory so we don't need ~/.claude/skills/tier1.
        tier1_dir = tmp_path / "tier1"
        tier1_dir.mkdir()
        skill_file = tier1_dir / "my_skill.md"
        skill_file.write_text("# Tier-1 skill\n")

        import depthfusion.hooks.post_tool_use as module
        monkeypatch.setattr(module, "_TIER1_DIR", tier1_dir)

        assert floor_check_before_skill_edit({"file": str(skill_file)}) is False

    def test_nonexistent_file_is_allowed(self, tmp_path):
        # File doesn't exist yet; can't read content → conservative allow.
        assert floor_check_before_skill_edit({"file": str(tmp_path / "new_file.py")}) is True

    def test_empty_patch_is_allowed(self):
        assert floor_check_before_skill_edit({}) is True

    def test_patch_using_path_key_is_recognised(self, tmp_path):
        target = tmp_path / "ordinary.py"
        target.write_text("x = 1\n")
        assert floor_check_before_skill_edit({"path": str(target)}) is True

    def test_exception_in_check_is_fail_closed(self, monkeypatch):
        # Simulate Path.is_file() raising an unexpected error.
        def bad_resolve(self):  # noqa: ANN001
            raise RuntimeError("simulated error")

        monkeypatch.setattr(Path, "resolve", bad_resolve)
        # Should not raise; returns False (blocked) as the safe default.
        result = floor_check_before_skill_edit({"file": "/some/path.py"})
        assert result is False


# ---------------------------------------------------------------------------
# fork_review_pass (AC-1)
# ---------------------------------------------------------------------------


class TestForkReviewPass:
    def test_not_called_when_env_var_unset(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_PERTURN_REVIEW", raising=False)
        with patch("subprocess.run") as mock_run:
            result = fork_review_pass({"tool_name": "Read", "session_id": "s1"})
        mock_run.assert_not_called()
        assert result == []

    def test_not_called_when_env_var_false(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "false")
        with patch("subprocess.run") as mock_run:
            result = fork_review_pass({"tool_name": "Edit", "session_id": "s2"})
        mock_run.assert_not_called()
        assert result == []

    def test_called_when_env_var_true(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "true")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "[]"
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = fork_review_pass({"tool_name": "Read", "session_id": "s3"})
        mock_run.assert_called_once()
        assert result == []

    def test_cache_isolated_env_passed(self, monkeypatch):
        """AC-1: subprocess must receive ANTHROPIC_NO_SESSION_REUSE=1."""
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "1")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "[]"
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            fork_review_pass({"tool_name": "Bash", "session_id": "s4"})
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"]["ANTHROPIC_NO_SESSION_REUSE"] == "1"

    def test_returns_valid_patches(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "yes")
        patches = [
            {"file": "/tmp/foo.py", "change": "add docstring", "rationale": "clarity"},
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(patches)
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc):
            result = fork_review_pass({"tool_name": "Read", "session_id": "s5"})
        # /tmp/foo.py is not Tier-1, so patch should pass.
        assert len(result) == 1
        assert result[0]["file"] == "/tmp/foo.py"

    def test_tier1_patch_is_filtered_out(self, tmp_path, monkeypatch):
        """AC-2: Tier-1 edits proposed by the review pass are blocked."""
        tier1_skill = tmp_path / "tier1_skill.md"
        tier1_skill.write_text("risk_tier: tier1\n# some content\n")

        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "true")
        patches = [
            {
                "file": str(tier1_skill),
                "change": "refactor",
                "rationale": "improvement",
            },
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(patches)
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc):
            result = fork_review_pass({"tool_name": "Edit", "session_id": "s6"})
        # Tier-1 patch must be blocked.
        assert result == []

    def test_exception_is_swallowed_fail_closed(self, monkeypatch):
        """AC-1/AC-2: any exception is caught and logged, not re-raised."""
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "true")
        with patch("subprocess.run", side_effect=RuntimeError("subprocess exploded")):
            # Must not raise.
            result = fork_review_pass({"tool_name": "Bash", "session_id": "s7"})
        assert result == []

    def test_malformed_json_returns_empty(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "true")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "not json at all"
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc):
            result = fork_review_pass({"tool_name": "Read", "session_id": "s8"})
        assert result == []

    def test_non_zero_exit_returns_empty(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "true")
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error"
        with patch("subprocess.run", return_value=mock_proc):
            result = fork_review_pass({"tool_name": "Glob", "session_id": "s9"})
        assert result == []

    def test_patches_missing_required_keys_are_skipped(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "1")
        patches = [
            {"file": "/tmp/foo.py", "change": "add docstring"},  # missing rationale
            {"file": "/tmp/bar.py", "change": "fix", "rationale": "good"},  # valid
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(patches)
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc):
            result = fork_review_pass({"tool_name": "Read", "session_id": "s10"})
        assert len(result) == 1
        assert result[0]["file"] == "/tmp/bar.py"


# ---------------------------------------------------------------------------
# handle_post_tool_use wiring (AC-1)
# ---------------------------------------------------------------------------


class TestHandlePostToolUseWiring:
    """Verify fork_review_pass is invoked (or not) from handle_post_tool_use."""

    def test_fork_returns_empty_when_review_disabled(self, monkeypatch, tmp_path):
        """When DEPTHFUSION_PERTURN_REVIEW is unset, fork_review_pass is called but
        returns [] immediately (the internal env-var gate keeps it a no-op).
        handle_post_tool_use always calls fork_review_pass so the outer try/except
        works correctly regardless of ambient-capture state."""
        monkeypatch.delenv("DEPTHFUSION_PERTURN_REVIEW", raising=False)
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "false")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        with patch("subprocess.run") as mock_subprocess:
            handle_post_tool_use({"tool_name": "Read", "session_id": "wiring-1"})
        # subprocess must NOT be called because the env-var gate fires inside fork_review_pass.
        mock_subprocess.assert_not_called()

    def test_fork_called_when_review_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "false")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        with patch(
            "depthfusion.hooks.post_tool_use.fork_review_pass",
            return_value=[],
        ) as mock_fork:
            handle_post_tool_use({"tool_name": "Read", "session_id": "wiring-2"})
        mock_fork.assert_called_once()

    def test_fork_exception_does_not_propagate(self, monkeypatch, tmp_path):
        """Fail-closed: fork_review_pass error must never raise out of handle_post_tool_use."""
        monkeypatch.setenv("DEPTHFUSION_PERTURN_REVIEW", "true")
        monkeypatch.setenv("DEPTHFUSION_AMBIENT_CAPTURE", "false")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        # fork_review_pass itself is fail-closed, but even if somehow an exception
        # escapes, handle_post_tool_use must swallow it.
        with patch(
            "depthfusion.hooks.post_tool_use.fork_review_pass",
            side_effect=RuntimeError("unexpected"),
        ):
            # Should not raise.
            handle_post_tool_use({"tool_name": "Edit", "session_id": "wiring-3"})
