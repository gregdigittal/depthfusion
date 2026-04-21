"""git_post_commit.py — Git post-commit hook capture (CM-3).

When installed as `.git/hooks/post-commit` (or appended to an existing one),
this script writes a discovery file capturing the commit's metadata and diff
summary so future sessions can recall what was changed and why.

CM-3 contract (S-46):
  - Writes {date}-{project}-commit-{sha7}.md in ~/.claude/shared/discoveries/
  - Idempotent: file path includes the SHA7, so the same commit never writes twice
  - Appends-friendly: detects existing DepthFusion hook block via sentinel comment
  - Completes in < 500ms on ≤ 50-file commits

Usage as a standalone script (invoked by git):
    python3 -m depthfusion.hooks.git_post_commit

Environment variables:
  DEPTHFUSION_PROJECT   — project slug (auto-detected from git remote URL if absent)
  DEPTHFUSION_HOOK_DIR  — override discovery output dir

Spec: docs/plans/v0.5/01-assessment.md §CM-3
Backlog: T-140, T-141, T-143
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

def _default_discoveries_dir() -> Path:
    """Resolve `~/.claude/shared/discoveries/` at call time.

    Runtime-resolution pattern — see `capture/decision_extractor.py`,
    `capture/negative_extractor.py`, and `capture/pruner.py` for the
    same idiom. Prevents the freeze-at-import bug where tests can't
    redirect `Path.home()` via monkeypatch because the module-level
    constant was already computed at import time.
    """
    return Path.home() / ".claude" / "shared" / "discoveries"


# Deprecated module-level constant — retained for external importers.
_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"
_MAX_DIFF_LINES = 80   # cap diff summary size
_MAX_MESSAGE_CHARS = 1000
_TIMEOUT_SECONDS = 4   # git calls must not stall the commit


def _run_git(*args: str, cwd: Path | None = None, timeout: int = _TIMEOUT_SECONDS) -> str:
    """Run a git command and return stdout. Returns empty string on error."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def get_commit_info(cwd: Path | None = None) -> dict:
    """Read HEAD commit metadata via git commands.

    Returns a dict with: sha, sha7, message, author, files_changed, diff_summary.
    """
    sha = _run_git("rev-parse", "HEAD", cwd=cwd)
    sha7 = sha[:7] if sha else "unknown"
    message = _run_git("log", "-1", "--pretty=%B", cwd=cwd)[:_MAX_MESSAGE_CHARS]
    author = _run_git("log", "-1", "--pretty=%an <%ae>", cwd=cwd)

    # Compact diff stat: N files changed, M insertions(+), K deletions(-)
    diff_stat = _run_git("diff", "--stat", "HEAD~1", "HEAD", "--no-color", cwd=cwd)
    if not diff_stat:
        # First commit has no parent — use diff against empty tree
        diff_stat = _run_git(
            "diff", "--stat", "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
            "HEAD", "--no-color", cwd=cwd,
        )

    # Limit diff stat to _MAX_DIFF_LINES
    stat_lines = diff_stat.splitlines()
    if len(stat_lines) > _MAX_DIFF_LINES:
        trimmed = len(stat_lines) - _MAX_DIFF_LINES
        stat_lines = stat_lines[:_MAX_DIFF_LINES] + [f"... ({trimmed} more lines)"]
        diff_stat = "\n".join(stat_lines)

    files_changed_line = stat_lines[-1] if stat_lines else ""

    return {
        "sha": sha,
        "sha7": sha7,
        "message": message,
        "author": author,
        "files_changed": files_changed_line,
        "diff_summary": diff_stat,
    }


def detect_project(cwd: Path | None = None) -> str:
    """Detect project slug from DEPTHFUSION_PROJECT env var or git remote URL."""
    project = os.environ.get("DEPTHFUSION_PROJECT", "").strip()
    if project:
        return re.sub(r"[^a-z0-9-]", "-", project.lower())[:40].strip("-")

    # Try to extract from origin URL
    remote = _run_git("config", "--get", "remote.origin.url", cwd=cwd)
    if remote:
        # https://github.com/user/repo.git  or  git@github.com:user/repo.git
        name = re.sub(r"\.git$", "", remote.split("/")[-1]).strip()
        if name:
            return re.sub(r"[^a-z0-9-]", "-", name.lower())[:40].strip("-")

    # Fallback: use directory name
    try:
        return re.sub(r"[^a-z0-9-]", "-", (cwd or Path.cwd()).name.lower())[:40]
    except Exception:
        return "unknown"


def write_commit_discovery(
    commit: dict,
    project: str,
    output_dir: Path | None = None,
) -> Path | None:
    """Write commit metadata to a discovery file.

    Idempotent: file includes SHA7, so the same commit never writes twice.

    Returns output Path on success, None if already exists or nothing to write.
    """
    if not commit.get("sha7") or commit["sha7"] == "unknown":
        return None

    out_dir = output_dir or _default_discoveries_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    slug = re.sub(r"[^a-z0-9-]", "-", project.lower())[:40].strip("-") or "unknown"
    sha7 = commit["sha7"]
    filename = f"{today}-{slug}-commit-{sha7}.md"
    output_path = out_dir / filename

    if output_path.exists():
        logger.debug("Commit discovery %s already exists, skipping", filename)
        # S-60 / T-189: emit skip event so the stream reflects every
        # invocation — operators can see "commit hook fired, no new
        # discovery written" vs silent behaviour.
        _emit_capture_event(
            capture_mechanism="git_post_commit",
            project=project, session_id=sha7,
            write_success=False, entries_written=0,
            file_path=str(output_path),
        )
        return None

    message = commit.get("message", "").strip()
    author = commit.get("author", "").strip()
    diff_summary = commit.get("diff_summary", "").strip()
    files_changed = commit.get("files_changed", "").strip()

    lines = [
        "---",
        f"project: {project}",
        f"date: {today}",
        f"sha: {commit.get('sha', sha7)}",
        f"sha7: {sha7}",
        "type: commit",
        "---",
        "",
        f"# Commit: {project} {sha7}",
        "",
    ]
    if author:
        lines += [f"**Author:** {author}", ""]
    lines += ["## Message", "", message, ""]
    if files_changed:
        lines += [f"**Changes:** {files_changed}", ""]
    if diff_summary:
        lines += ["## Diff Summary", "", "```", diff_summary, "```", ""]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote commit discovery %s", output_path.name)
    _emit_capture_event(
        capture_mechanism="git_post_commit",
        project=project, session_id=sha7,
        write_success=True, entries_written=1,
        file_path=str(output_path),
    )
    return output_path


def _emit_capture_event(**kwargs) -> None:
    """Thin wrapper around `depthfusion.capture._metrics.emit_capture_event`
    with an extra layer of exception-swallowing.

    Git post-commit hooks MUST return 0 — a metrics failure here would
    bubble into `run_hook` which could block the developer's git commit.
    The shared helper already swallows, but this wrapper guards the
    import path itself so even a broken metrics module can't crash the
    git-commit flow.
    """
    try:
        from depthfusion.capture._metrics import emit_capture_event
        emit_capture_event(**kwargs)
    except Exception:  # noqa: BLE001 — git hook must never block commit
        pass


def run_hook(cwd: Path | None = None, output_dir: Path | None = None) -> int:
    """Main hook logic. Returns 0 on success, non-zero on error.

    This function is the entry point for both the CLI script and tests.
    Errors are caught to ensure the git commit operation always completes.
    """
    try:
        commit = get_commit_info(cwd=cwd)
        project = detect_project(cwd=cwd)
        write_commit_discovery(commit, project=project, output_dir=output_dir)
        return 0
    except Exception as exc:  # noqa: BLE001
        # Never block a git commit due to hook failure
        logger.debug("git_post_commit hook error: %s", exc)
        return 0


def main() -> None:
    """Entry point when invoked as __main__ by git."""
    logging.basicConfig(level=logging.WARNING)
    sys.exit(run_hook())


if __name__ == "__main__":
    main()


__all__ = [
    "get_commit_info",
    "detect_project",
    "write_commit_discovery",
    "run_hook",
]
