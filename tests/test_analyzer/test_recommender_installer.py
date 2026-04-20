"""Tests for InstallRecommender and DepthFusionInstaller — including T-142 git hook support."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from depthfusion.analyzer.compatibility import GREEN, RED, YELLOW
from depthfusion.analyzer.installer import _HOOK_SENTINEL, DepthFusionInstaller
from depthfusion.analyzer.recommender import InstallRecommender


def _make_check_results(**overrides) -> dict:
    """Create a minimal check_results dict, all GREEN by default."""
    base = {f"C{i}": {"status": GREEN, "message": f"C{i} ok", "detail": ""} for i in range(1, 12)}
    base.update(overrides)
    return base


def _make_git_repo(path: Path) -> Path:
    """Create a minimal fake git repo at *path*."""
    git_dir = path / ".git"
    (git_dir / "hooks").mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Existing recommender tests (unchanged)
# ---------------------------------------------------------------------------

def test_recommender_empty_steps_for_all_green():
    rec = InstallRecommender()
    results = _make_check_results()
    steps = rec.recommend(results)
    assert steps == []


def test_recommender_returns_step_for_red_constraint():
    rec = InstallRecommender()
    results = _make_check_results(C2={"status": RED, "message": "Too many tools", "detail": "fix it"})
    steps = rec.recommend(results)
    assert len(steps) == 1
    assert steps[0]["priority"] == "critical"
    assert "C2" in steps[0]["action"]


def test_recommender_returns_step_for_yellow_constraint():
    rec = InstallRecommender()
    results = _make_check_results(C3={"status": YELLOW, "message": "Skills dir missing", "detail": ""})
    steps = rec.recommend(results)
    assert len(steps) == 1
    assert steps[0]["priority"] == "recommended"
    assert "C3" in steps[0]["action"]


def test_recommender_multiple_issues():
    rec = InstallRecommender()
    results = _make_check_results(
        C2={"status": RED, "message": "Too many tools", "detail": ""},
        C6={"status": RED, "message": "No venv", "detail": "create one"},
        C7={"status": YELLOW, "message": "No recall", "detail": "add it"},
    )
    steps = rec.recommend(results)
    assert len(steps) == 3
    priorities = [s["priority"] for s in steps]
    assert priorities.count("critical") == 2
    assert priorities.count("recommended") == 1


def test_installer_dry_run_returns_prefixed_actions():
    installer = DepthFusionInstaller(dry_run=True)
    steps = [
        {"action": "Fix C2: too many tools", "detail": "", "priority": "critical"},
        {"action": "Review C7: no recall", "detail": "add it", "priority": "recommended"},
    ]
    completed = installer.install(steps)
    assert len(completed) == 2
    for item in completed:
        assert "[DRY RUN]" in item


def test_installer_dry_run_does_not_modify_filesystem(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    installer = DepthFusionInstaller(dry_run=True)
    steps = [{"action": f"Would create {sentinel}", "detail": "", "priority": "optional"}]
    installer.install(steps)
    assert not sentinel.exists()


def test_installer_live_mode_returns_actions():
    installer = DepthFusionInstaller(dry_run=False)
    steps = [{"action": "Do something", "detail": "", "priority": "optional"}]
    completed = installer.install(steps)
    assert completed == ["Do something"]


def test_installer_empty_steps_returns_empty():
    installer = DepthFusionInstaller(dry_run=True)
    assert installer.install([]) == []


# ---------------------------------------------------------------------------
# T-142: git hook detection and step generation
# ---------------------------------------------------------------------------

class TestDetectGitRepos:
    def test_detects_root_repo(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller()
        repos = installer.detect_git_repos(tmp_path)
        assert tmp_path in repos

    def test_detects_child_repos(self, tmp_path):
        child = tmp_path / "my-project"
        child.mkdir()
        _make_git_repo(child)
        installer = DepthFusionInstaller()
        repos = installer.detect_git_repos(tmp_path)
        assert child in repos

    def test_empty_dir_returns_empty(self, tmp_path):
        installer = DepthFusionInstaller()
        repos = installer.detect_git_repos(tmp_path)
        assert repos == []

    def test_non_git_dirs_excluded(self, tmp_path):
        (tmp_path / "not-a-repo").mkdir()
        installer = DepthFusionInstaller()
        repos = installer.detect_git_repos(tmp_path)
        assert repos == []


class TestCheckGitHookInstalled:
    def test_returns_false_when_no_hook_file(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller()
        assert installer.check_git_hook_installed(tmp_path) is False

    def test_returns_false_when_hook_has_no_sentinel(self, tmp_path):
        _make_git_repo(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        hook.write_text("#!/bin/bash\necho hello\n")
        installer = DepthFusionInstaller()
        assert installer.check_git_hook_installed(tmp_path) is False

    def test_returns_true_when_sentinel_present(self, tmp_path):
        _make_git_repo(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        hook.write_text(f"#!/bin/bash\n{_HOOK_SENTINEL}\npython3 -m depthfusion.hooks.git_post_commit\n")
        installer = DepthFusionInstaller()
        assert installer.check_git_hook_installed(tmp_path) is True

    def test_returns_false_when_no_git_dir(self, tmp_path):
        # tmp_path has no .git at all
        installer = DepthFusionInstaller()
        assert installer.check_git_hook_installed(tmp_path) is False


class TestSuggestGitHookSteps:
    def test_returns_step_for_unhooked_repo(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller()
        steps = installer.suggest_git_hook_steps(tmp_path)
        assert len(steps) == 1
        step = steps[0]
        assert step["action_type"] == "install_git_hook"
        assert step["priority"] == "recommended"
        assert str(tmp_path) == step["repo_path"]

    def test_no_step_when_already_installed(self, tmp_path):
        _make_git_repo(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        hook.write_text(f"#!/bin/bash\n{_HOOK_SENTINEL}\n")
        installer = DepthFusionInstaller()
        steps = installer.suggest_git_hook_steps(tmp_path)
        assert steps == []

    def test_no_step_when_no_repos_found(self, tmp_path):
        # plain empty directory — no .git
        installer = DepthFusionInstaller()
        steps = installer.suggest_git_hook_steps(tmp_path)
        assert steps == []

    def test_step_detail_mentions_disable_instructions(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller()
        steps = installer.suggest_git_hook_steps(tmp_path)
        assert len(steps) == 1
        assert "disable" in steps[0]["detail"].lower() or "remove" in steps[0]["detail"].lower()

    def test_multiple_repos_both_get_steps(self, tmp_path):
        repo_a = tmp_path / "alpha"
        repo_b = tmp_path / "beta"
        repo_a.mkdir()
        repo_b.mkdir()
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        installer = DepthFusionInstaller()
        steps = installer.suggest_git_hook_steps(tmp_path)
        assert len(steps) == 2

    def test_mixed_hooked_and_unhooked(self, tmp_path):
        repo_a = tmp_path / "already-hooked"
        repo_b = tmp_path / "needs-hook"
        repo_a.mkdir()
        repo_b.mkdir()
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        hook = repo_a / ".git" / "hooks" / "post-commit"
        hook.write_text(f"#!/bin/bash\n{_HOOK_SENTINEL}\n")
        installer = DepthFusionInstaller()
        steps = installer.suggest_git_hook_steps(tmp_path)
        assert len(steps) == 1
        assert "needs-hook" in steps[0]["repo_path"]


class TestInstallGitHookLiveMode:
    def test_dry_run_does_not_call_script(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller(dry_run=True)
        step = {
            "action": "Install git hook",
            "action_type": "install_git_hook",
            "repo_path": str(tmp_path),
            "priority": "recommended",
            "detail": "",
        }
        completed = installer.install([step])
        assert len(completed) == 1
        assert "[DRY RUN]" in completed[0]

    def test_live_mode_calls_script(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller(dry_run=False)
        step = {
            "action": "Install git hook",
            "action_type": "install_git_hook",
            "repo_path": str(tmp_path),
            "priority": "recommended",
            "detail": "",
        }
        # Mock subprocess.run to simulate successful hook installation
        with patch("depthfusion.analyzer.installer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Created .git/hooks/post-commit with DepthFusion block."
            mock_run.return_value.stderr = ""
            completed = installer.install([step])
        assert len(completed) == 1
        assert "DRY RUN" not in completed[0]
        assert mock_run.called

    def test_live_mode_handles_script_failure(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller(dry_run=False)
        step = {
            "action": "Install git hook",
            "action_type": "install_git_hook",
            "repo_path": str(tmp_path),
            "priority": "recommended",
            "detail": "",
        }
        with patch("depthfusion.analyzer.installer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "Error: not a git repo"
            completed = installer.install([step])
        assert "[ERROR]" in completed[0]

    def test_live_mode_handles_missing_script(self, tmp_path):
        _make_git_repo(tmp_path)
        installer = DepthFusionInstaller(dry_run=False)
        step = {
            "action": "Install git hook",
            "action_type": "install_git_hook",
            "repo_path": str(tmp_path),
            "priority": "recommended",
            "detail": "",
        }
        with patch("depthfusion.analyzer.installer._SCRIPT_RELPATH",
                   new=tmp_path / "nonexistent.sh"):
            completed = installer.install([step])
        assert "[ERROR]" in completed[0]
