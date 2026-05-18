"""SessionStart auto-recall injection handler — S-111.

Called by ~/.claude/hooks/depthfusion-session-start.sh at the start of every
Claude Code session. Constructs a seed query from the current project and recent
git history, then calls recall_relevant internally and publishes the top results
as high-priority ContextItems so the session starts warm.

Design constraints (AC-5, AC-6): ALWAYS exits 0. Any exception — including an
unreachable MCP server — is swallowed so the hook never blocks a Claude session.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


def _detect_project_name(cwd: Path) -> str:
    """Return a project slug from git remote URL or directory name."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        )
        if result.returncode == 0 and result.stdout.strip():
            remote = result.stdout.strip()
            # Extract repo name from URL (handles HTTPS and SSH formats)
            name = remote.rstrip("/").split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            return name or cwd.name
    except Exception:
        pass
    return cwd.name


def _recent_git_messages(cwd: Path, n: int = 5) -> list[str]:
    """Return the last N commit summary lines from the CWD repo."""
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={n}", "--pretty=format:%s"],
            capture_output=True, text=True, timeout=3, cwd=cwd,
        )
        if result.returncode == 0:
            return [m.strip() for m in result.stdout.splitlines() if m.strip()]
    except Exception:
        pass
    return []


def _build_seed_query(project_name: str, git_messages: list[str]) -> str:
    """Construct a seed query that captures recent work and project context."""
    parts = [project_name]
    if git_messages:
        # Take first 3 commit messages to anchor the recency signal
        parts.extend(git_messages[:3])
    return " ".join(parts)


def _recall_and_seed(
    session_id: str,
    top_k: int = 3,
    snippet_len: int = 800,
) -> int:
    """Run recall and publish seed items. Returns count of items published."""
    import hashlib
    import time

    from depthfusion.core.types import ContextItem
    from depthfusion.mcp.server import _tool_recall_impl
    from depthfusion.router.bus import FileBus

    cwd = Path.cwd()
    project_name = _detect_project_name(cwd)
    git_messages = _recent_git_messages(cwd)
    query = _build_seed_query(project_name, git_messages)

    result_json = _tool_recall_impl({
        "query": query,
        "top_k": top_k,
        "snippet_len": snippet_len,
    })
    result = json.loads(result_json)
    blocks = result.get("blocks", [])

    if not blocks:
        return 0

    bus_dir = Path(
        os.environ.get("DEPTHFUSION_BUS_FILE_DIR", "~/.claude/context-bus")
    ).expanduser()
    bus_dir.mkdir(parents=True, exist_ok=True)
    bus = FileBus(bus_dir=bus_dir)

    published = 0
    for block in blocks:
        content = block.get("snippet") or block.get("content") or ""
        if not content:
            continue
        ts_ms = int(time.time() * 1000)
        suffix = hashlib.md5(f"{session_id}{content[:64]}{ts_ms}".encode()).hexdigest()[:8]
        item_id = f"seed-{session_id[:16]}-{suffix}"
        item = ContextItem(
            item_id=item_id,
            content=content,
            source_agent="depthfusion-session-seed",
            tags=["session-seed", session_id],
            importance=0.9,
        )
        bus.publish(item)
        published += 1

    return published


def handle_session_start(payload: dict) -> None:
    """Handle one SessionStart event. No-op if auto-recall is disabled."""
    if not _env_bool("DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", True):
        return

    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or "unknown"
    )
    top_k = int(os.environ.get("DEPTHFUSION_AUTO_RECALL_TOP_K", "3") or 3)
    snippet_len = int(os.environ.get("DEPTHFUSION_AUTO_RECALL_SNIPPET_LEN", "800") or 800)

    try:
        count = _recall_and_seed(session_id, top_k=top_k, snippet_len=snippet_len)
        logger.debug("Session seed: published %d item(s) for session %s", count, session_id)
    except Exception as exc:
        logger.debug("Session seed failed (non-fatal): %s", exc)


def main() -> None:
    """Entry point: read JSON from stdin, handle, exit 0 always (AC-5)."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handle_session_start(payload)
    except Exception as exc:
        logger.debug("SessionStart hook top-level error (non-fatal): %s", exc)
    sys.exit(0)


if __name__ == "__main__":
    main()
