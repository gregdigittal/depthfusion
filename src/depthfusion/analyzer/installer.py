"""DepthFusionInstaller — executes or simulates install steps.

v0.5.0 T-142: extended with git post-commit hook opt-in support (CM-3).

The installer can now:
  1. Detect nearby git repos (detect_git_repos)
  2. Check whether the DepthFusion hook block is already present (check_git_hook_installed)
  3. Generate recommended install steps for unhooked repos (suggest_git_hook_steps)
  4. Execute the install-git-hook.sh script in live mode when action_type == "install_git_hook"
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Sentinel string that install-git-hook.sh writes into post-commit hooks.
# If this is present, the hook is already installed — no action needed.
_HOOK_SENTINEL = "# DepthFusion post-commit hook"

# Path to the installer script relative to the package root.
# Resolved at runtime from the package location so it works regardless of cwd.
_SCRIPT_RELPATH = Path(__file__).parent.parent.parent.parent / "scripts" / "install-git-hook.sh"


class DepthFusionInstaller:
    """Executes install steps produced by InstallRecommender.

    dry_run=True (default) logs actions without modifying the filesystem.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Core install dispatcher
    # ------------------------------------------------------------------

    def install(self, steps: list[dict]) -> list[str]:
        """Execute install steps.

        Recognised action_type values (step["action_type"]):
          - "install_git_hook"  — runs install-git-hook.sh for step["repo_path"]
          - (any other / absent) — generic log-and-mark behaviour

        dry_run=True logs actions without executing.
        Returns list of completed (or simulated) action descriptions.
        """
        completed: list[str] = []
        for step in steps:
            action = step.get("action", "unknown action")
            detail = step.get("detail", "")
            priority = step.get("priority", "optional")
            action_type = step.get("action_type", "")

            if self.dry_run:
                msg = f"[DRY RUN] Would execute ({priority}): {action}"
                if detail:
                    msg += f" — {detail}"
                logger.info(msg)
                completed.append(f"[DRY RUN] {action}")
            else:
                logger.info(f"Executing ({priority}): {action}")
                if action_type == "install_git_hook":
                    result = self._run_git_hook_installer(step.get("repo_path", ""))
                    completed.append(result)
                else:
                    completed.append(action)

        return completed

    # ------------------------------------------------------------------
    # Git hook detection helpers (T-142)
    # ------------------------------------------------------------------

    def detect_git_repos(self, search_path: Path | None = None) -> list[Path]:
        """Find .git directories under *search_path* (non-recursive, depth ≤ 1).

        Looks for:
          - search_path/.git  (the search_path itself is a git repo)
          - search_path/*/.git  (immediate children are git repos)

        Returns a list of repo root paths (the parent of .git).
        """
        base = search_path or Path.cwd()
        repos: list[Path] = []

        # Is the search_path itself a git repo?
        if (base / ".git").is_dir():
            repos.append(base)

        # Any immediate children that are git repos?
        try:
            for child in base.iterdir():
                if child.is_dir() and (child / ".git").is_dir():
                    repos.append(child)
        except PermissionError:
            pass

        return repos

    def check_git_hook_installed(self, repo_path: Path) -> bool:
        """Return True if the DepthFusion sentinel is present in repo's post-commit hook.

        A repo that has no post-commit hook at all returns False (not yet installed).
        """
        hook_file = repo_path / ".git" / "hooks" / "post-commit"
        if not hook_file.exists():
            return False
        try:
            return _HOOK_SENTINEL in hook_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    def suggest_git_hook_steps(self, search_path: Path | None = None) -> list[dict]:
        """Generate recommended install steps for git repos that lack the hook.

        For each detected repo that does NOT already have the DepthFusion hook:
          - Returns one step dict with action_type="install_git_hook"

        Returns an empty list when all detected repos already have the hook,
        or when no git repos are found.
        """
        repos = self.detect_git_repos(search_path)
        steps: list[dict] = []

        for repo in repos:
            if self.check_git_hook_installed(repo):
                continue

            steps.append({
                "action": f"Install DepthFusion post-commit hook in {repo.name}",
                "detail": (
                    f"Run: bash scripts/install-git-hook.sh {repo}\n"
                    "The hook writes commit metadata to ~/.claude/shared/discoveries/ "
                    "after every git commit. It is safe to add to repos with existing "
                    "post-commit hooks (appends, never overwrites). Remove the "
                    "'# DepthFusion post-commit hook' block from .git/hooks/post-commit "
                    "at any time to disable."
                ),
                "priority": "recommended",
                "action_type": "install_git_hook",
                "repo_path": str(repo),
            })

        return steps

    # ------------------------------------------------------------------
    # Private: live-mode git hook executor
    # ------------------------------------------------------------------

    def _run_git_hook_installer(self, repo_path: str) -> str:
        """Run install-git-hook.sh for *repo_path*. Returns a status message."""
        script = _SCRIPT_RELPATH
        if not script.exists():
            msg = f"install-git-hook.sh not found at {script}"
            logger.error(msg)
            return f"[ERROR] {msg}"

        try:
            result = subprocess.run(
                ["bash", str(script), repo_path],
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                logger.info("Git hook installed: %s", output)
                return output or f"DepthFusion hook installed in {repo_path}"
            msg = f"install-git-hook.sh failed (exit {result.returncode}): {output}"
            logger.error(msg)
            return f"[ERROR] {msg}"
        except subprocess.TimeoutExpired:
            return "[ERROR] install-git-hook.sh timed out"
        except OSError as exc:
            return f"[ERROR] Could not run install-git-hook.sh: {exc}"
