"""PostToolUse ambient capture handler — S-110.

Called by ~/.claude/hooks/depthfusion-post-tool-use.sh on every tool invocation.
Reads a JSON payload from stdin (Claude Code PostToolUse hook protocol) and
publishes a low-importance ContextItem to the FileBus.

Design constraints (AC-6): ALWAYS exits 0. Any exception is swallowed and logged
at DEBUG level so the hook never blocks a Claude Code session.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Tools that produce purely read-side effects and are cheap enough to skip.
# The user can extend this list via DEPTHFUSION_AMBIENT_SKIP_TOOLS.
_DEFAULT_SKIP_TOOLS: frozenset[str] = frozenset()


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


def _skip_tools() -> frozenset[str]:
    raw = os.environ.get("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "")
    user_tools = frozenset(t.strip() for t in raw.split(",") if t.strip())
    return _DEFAULT_SKIP_TOOLS | user_tools


def _extract_files(tool_name: str, tool_input: dict) -> tuple[list[str], list[str]]:
    """Extract files_read and files_modified from tool call metadata."""
    files_read: list[str] = []
    files_modified: list[str] = []

    if tool_name in ("Read",):
        path = tool_input.get("file_path") or tool_input.get("path")
        if path:
            files_read.append(str(path))
    elif tool_name in ("Glob",):
        pattern = tool_input.get("pattern") or tool_input.get("path")
        if pattern:
            files_read.append(str(pattern))
    elif tool_name in ("Grep",):
        path = tool_input.get("path") or tool_input.get("include")
        if path:
            files_read.append(str(path))
    elif tool_name in ("Write", "Edit", "NotebookEdit"):
        path = tool_input.get("file_path")
        if path:
            files_modified.append(str(path))
    elif tool_name == "Bash":
        # File paths embedded in arbitrary shell commands are not reliably
        # extractable without a shell parser. Record no paths for Bash.
        pass

    return files_read[:20], files_modified[:20]


def handle_post_tool_use(payload: dict) -> None:
    """Handle one PostToolUse event. Does nothing if ambient capture is disabled."""
    if not _env_bool("DEPTHFUSION_AMBIENT_CAPTURE", True):
        return

    tool_name = payload.get("tool_name", "")
    if not tool_name:
        return

    if tool_name in _skip_tools():
        return

    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or "unknown"
    )
    tool_input = payload.get("tool_input") or {}
    exit_status = int(payload.get("exit_status") or 0)

    files_read, files_modified = _extract_files(tool_name, tool_input)

    try:
        from depthfusion.capture.auto_learn import build_ambient_item
        from depthfusion.router.bus import FileBus

        item = build_ambient_item(
            tool_name=tool_name,
            session_id=session_id,
            files_read=files_read,
            files_modified=files_modified,
            exit_status=exit_status,
        )
        bus_dir = Path(
            os.environ.get("DEPTHFUSION_BUS_FILE_DIR", "~/.claude/context-bus")
        ).expanduser()
        bus_dir.mkdir(parents=True, exist_ok=True)
        bus = FileBus(bus_dir=bus_dir)
        bus.publish(item)
    except Exception as exc:
        logger.debug("Ambient capture failed (non-fatal): %s", exc)


def main() -> None:
    """Entry point: read JSON from stdin, handle, exit 0 always (AC-6)."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handle_post_tool_use(payload)
    except Exception as exc:
        logger.debug("PostToolUse hook top-level error (non-fatal): %s", exc)
    sys.exit(0)


if __name__ == "__main__":
    main()
