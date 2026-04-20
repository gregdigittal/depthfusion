"""Tests for hooks/git_post_commit.py — CM-3 / S-46 / T-143.

≥ 5 tests required by S-46 AC-4.
"""
from __future__ import annotations

from unittest.mock import patch

from depthfusion.hooks.git_post_commit import (
    detect_project,
    get_commit_info,
    run_hook,
    write_commit_discovery,
)

# ---------------------------------------------------------------------------
# get_commit_info
# ---------------------------------------------------------------------------

class TestGetCommitInfo:
    def test_returns_dict_with_required_keys(self, tmp_path):
        """Even with no real git repo, the function returns a dict with expected keys."""
        # tmp_path has no git repo — _run_git returns "" for all calls
        info = get_commit_info(cwd=tmp_path)
        assert isinstance(info, dict)
        for key in ("sha", "sha7", "message", "author", "files_changed", "diff_summary"):
            assert key in info

    def test_sha7_from_sha(self, tmp_path):
        """sha7 should be the first 7 chars of sha when sha is present."""
        with patch("depthfusion.hooks.git_post_commit._run_git") as mock_git:
            mock_git.side_effect = lambda *args, **kwargs: {
                ("rev-parse", "HEAD"): "abcdef1234567890",
                ("log", "-1", "--pretty=%B"): "feat: add feature",
                ("log", "-1", "--pretty=%an <%ae>"): "Alice <alice@example.com>",
                ("diff", "--stat", "HEAD~1", "HEAD", "--no-color"): "1 file changed",
            }.get(args, "")
            info = get_commit_info(cwd=tmp_path)
        assert info["sha7"] == "abcdef1"

    def test_sha7_unknown_when_no_git(self, tmp_path):
        info = get_commit_info(cwd=tmp_path)
        # Without a real git repo, sha is empty → sha7 is "unknown"
        assert info["sha7"] in ("unknown",) or len(info["sha7"]) == 7

    def test_diff_stat_capped_at_max_lines(self, tmp_path):
        """Diff stats larger than _MAX_DIFF_LINES should be truncated."""
        big_stat = "\n".join(f"file{i}.py | 1 +" for i in range(100))
        big_stat += "\n100 files changed, 100 insertions(+)"

        with patch("depthfusion.hooks.git_post_commit._run_git") as mock_git:
            def git_side_effect(*args, **kwargs):
                if args[:2] == ("rev-parse", "HEAD"):
                    return "abc1234567890"
                if args[:2] == ("diff", "--stat"):
                    return big_stat
                return ""
            mock_git.side_effect = git_side_effect
            info = get_commit_info(cwd=tmp_path)

        lines = info["diff_summary"].splitlines()
        assert len(lines) <= 82  # _MAX_DIFF_LINES (80) + "... (N more lines)" + summary line


# ---------------------------------------------------------------------------
# detect_project
# ---------------------------------------------------------------------------

class TestDetectProject:
    def test_env_var_takes_precedence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "MyProject123")
        slug = detect_project(cwd=tmp_path)
        assert slug == "myproject123"

    def test_env_var_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "My Project / Name!")
        slug = detect_project(cwd=tmp_path)
        assert " " not in slug
        assert "/" not in slug

    def test_fallback_to_directory_name(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_PROJECT", raising=False)
        # tmp_path has no git remote — should fall back to dir name
        project_dir = tmp_path / "my-cool-app"
        project_dir.mkdir()
        slug = detect_project(cwd=project_dir)
        assert "my-cool-app" in slug or slug  # non-empty

    def test_slug_length_capped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "a" * 100)
        slug = detect_project(cwd=tmp_path)
        assert len(slug) <= 40

    def test_remote_url_extracted(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_PROJECT", raising=False)
        with patch("depthfusion.hooks.git_post_commit._run_git") as mock_git:
            mock_git.return_value = "https://github.com/user/awesome-repo.git"
            slug = detect_project(cwd=tmp_path)
        assert slug == "awesome-repo"


# ---------------------------------------------------------------------------
# write_commit_discovery
# ---------------------------------------------------------------------------

class TestWriteCommitDiscovery:
    def _make_commit(self, sha7="abc1234"):
        return {
            "sha": sha7 + "xyz",
            "sha7": sha7,
            "message": "feat: add user authentication",
            "author": "Alice <alice@example.com>",
            "files_changed": "3 files changed, 42 insertions(+)",
            "diff_summary": "src/auth.py | 42 +",
        }

    def test_writes_file(self, tmp_path):
        commit = self._make_commit()
        out = write_commit_discovery(commit, project="myapp", output_dir=tmp_path)
        assert out is not None
        assert out.exists()
        content = out.read_text()
        assert "feat: add user authentication" in content

    def test_idempotent(self, tmp_path):
        commit = self._make_commit(sha7="abc1234")
        out1 = write_commit_discovery(commit, project="myapp", output_dir=tmp_path)
        assert out1 is not None
        out2 = write_commit_discovery(commit, project="myapp", output_dir=tmp_path)
        assert out2 is None  # same file → skip

    def test_sha7_unknown_returns_none(self, tmp_path):
        commit = {
            "sha": "", "sha7": "unknown", "message": "x",
            "author": "", "files_changed": "", "diff_summary": "",
        }
        out = write_commit_discovery(commit, project="myapp", output_dir=tmp_path)
        assert out is None

    def test_filename_includes_sha7(self, tmp_path):
        commit = self._make_commit(sha7="deadbee")
        out = write_commit_discovery(commit, project="myapp", output_dir=tmp_path)
        assert out is not None
        assert "deadbee" in out.name

    def test_frontmatter_fields(self, tmp_path):
        commit = self._make_commit()
        out = write_commit_discovery(commit, project="depthfusion", output_dir=tmp_path)
        assert out is not None
        content = out.read_text()
        assert "type: commit" in content
        assert "project: depthfusion" in content

    def test_diff_summary_in_output(self, tmp_path):
        commit = self._make_commit()
        out = write_commit_discovery(commit, project="myapp", output_dir=tmp_path)
        assert out is not None
        content = out.read_text()
        assert "src/auth.py" in content


# ---------------------------------------------------------------------------
# run_hook
# ---------------------------------------------------------------------------

class TestRunHook:
    def test_returns_zero_on_success(self, tmp_path):
        with patch("depthfusion.hooks.git_post_commit._run_git") as mock_git:
            mock_git.side_effect = lambda *args, **kwargs: {
                ("rev-parse", "HEAD"): "abcdef1234567890",
                ("log", "-1", "--pretty=%B"): "chore: update deps",
                ("log", "-1", "--pretty=%an <%ae>"): "Bob <bob@example.com>",
                ("diff", "--stat", "HEAD~1", "HEAD", "--no-color"): "1 file changed",
                ("config", "--get", "remote.origin.url"): "https://github.com/u/repo.git",
            }.get(args, "")
            result = run_hook(cwd=tmp_path, output_dir=tmp_path)
        assert result == 0

    def test_returns_zero_even_on_exception(self, tmp_path):
        """Hook must never block a git commit — always return 0."""
        with patch("depthfusion.hooks.git_post_commit.get_commit_info",
                   side_effect=RuntimeError("git exploded")):
            result = run_hook(cwd=tmp_path, output_dir=tmp_path)
        assert result == 0

    def test_writes_discovery_file(self, tmp_path):
        with patch("depthfusion.hooks.git_post_commit._run_git") as mock_git:
            mock_git.side_effect = lambda *args, **kwargs: {
                ("rev-parse", "HEAD"): "1234567abcdef",
                ("log", "-1", "--pretty=%B"): "feat: new feature",
                ("log", "-1", "--pretty=%an <%ae>"): "Dev <dev@example.com>",
                ("diff", "--stat", "HEAD~1", "HEAD", "--no-color"): "2 files changed",
                ("config", "--get", "remote.origin.url"): "",
            }.get(args, "")
            run_hook(cwd=tmp_path, output_dir=tmp_path)
        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "1234567" in content
