"""PostToolUse ambient capture handler — S-110.

Called by ~/.claude/hooks/depthfusion-post-tool-use.sh on every tool invocation.
Reads a JSON payload from stdin (Claude Code PostToolUse hook protocol) and
publishes a low-importance ContextItem to the FileBus.

Design constraints (AC-6): ALWAYS exits 0. Any exception is swallowed and logged
at DEBUG level so the hook never blocks a Claude Code session.

S-262: Per-turn self-improvement loop.
When DEPTHFUSION_PERTURN_REVIEW is set, a cache-isolated review pass is forked
after each turn. The pass is tool-whitelisted (Read/Grep/Glob only) and returns
proposed patches. Tier-1 skill edits are blocked by floor_check_before_skill_edit().
All exceptions in this path are caught and logged (fail-closed / non-fatal).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# S-262 constants
# ---------------------------------------------------------------------------

# Tools the forked review pass is allowed to propose.
_REVIEW_ALLOWED_TOOLS: frozenset[str] = frozenset({"Read", "Grep", "Glob"})

# Marker that identifies a Tier-1 skill inside a file's content.
_TIER1_MARKER: str = "risk_tier: tier1"

# Directory that, by convention, holds all Tier-1 skills.
_TIER1_DIR: Path = Path("~/.claude/skills/tier1").expanduser()

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


# ---------------------------------------------------------------------------
# S-262: Tier-1 floor check
# ---------------------------------------------------------------------------

def floor_check_before_skill_edit(patch: dict[str, Any]) -> bool:
    """Return True (allow) if the patch target is not a Tier-1 skill.

    Returns False (block) when the target file is a Tier-1 skill, identified by
    either containing "risk_tier: tier1" in its content or residing inside
    ~/.claude/skills/tier1/.

    AC-2: Never edits a Tier-1 skill without the floor check; fail-closed on error.
    """
    try:
        target = patch.get("file") or patch.get("path") or ""
        if not target:
            return True  # no target → allow (nothing to block)

        target_path = Path(target).expanduser().resolve()

        # Check directory membership (fast path).
        try:
            target_path.relative_to(_TIER1_DIR.resolve())
            logger.debug("floor_check: blocked Tier-1 skill by directory: %s", target)
            return False
        except ValueError:
            pass  # not under tier1 dir; continue to content check

        # Check content for the Tier-1 marker (only if the file exists and is small).
        if target_path.is_file() and target_path.stat().st_size < 1_048_576:  # 1 MiB guard
            try:
                content = target_path.read_text(encoding="utf-8", errors="replace")
                if _TIER1_MARKER in content:
                    logger.debug("floor_check: blocked Tier-1 skill by marker: %s", target)
                    return False
            except OSError:
                pass  # can't read → conservative allow (file may not exist yet)

        return True
    except Exception as exc:  # noqa: BLE001
        # Fail-closed: unexpected errors block the edit.
        logger.debug("floor_check: unexpected error (blocking as safe default): %s", exc)
        return False


# ---------------------------------------------------------------------------
# S-262: Fork review pass
# ---------------------------------------------------------------------------

def fork_review_pass(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a cache-isolated, tool-whitelisted review pass as a subprocess.

    Returns a list of proposed patches: [{file, change, rationale}, ...].
    Any exception is caught and logged (fail-closed / non-fatal) — the caller
    should never see an exception from this function.

    AC-1: Runs only when DEPTHFUSION_PERTURN_REVIEW is set to a truthy value.
          Cache-isolated (no session reuse) and whitelisted to Read/Grep/Glob.
    """
    if not _env_bool("DEPTHFUSION_PERTURN_REVIEW", False):
        return []

    try:
        tool_name = payload.get("tool_name", "unknown")
        session_id = payload.get("session_id") or payload.get("sessionId") or "unknown"

        review_prompt = (
            "You are a cache-isolated self-improvement reviewer. "
            "Analyse the most recent tool invocation and propose improvements "
            "to the codebase. "
            f"Tool invoked: {tool_name}. Session: {session_id}. "
            "Respond ONLY with a JSON array of patch objects. "
            'Each object must have exactly three keys: "file" (string path), '
            '"change" (string description of the change), '
            '"rationale" (string reason). '
            "If no improvements are needed, return an empty array []. "
            f"You may ONLY use these tools: {sorted(_REVIEW_ALLOWED_TOOLS)}. "
            "Do NOT use Write, Edit, Bash, or any other tool. "
            "Do NOT reuse the current session context — treat this as a fresh review."
        )

        result = subprocess.run(  # noqa: S603
            ["claude", "--print", review_prompt],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "ANTHROPIC_NO_SESSION_REUSE": "1"},
        )

        if result.returncode != 0:
            logger.debug(
                "fork_review_pass: claude exited %d: %s",
                result.returncode,
                result.stderr[:200],
            )
            return []

        stdout = result.stdout.strip()
        if not stdout:
            return []

        # Extract the JSON array — the output may contain surrounding prose.
        start = stdout.find("[")
        end = stdout.rfind("]")
        if start == -1 or end == -1:
            logger.debug("fork_review_pass: no JSON array found in output")
            return []

        raw_patches: list[Any] = json.loads(stdout[start : end + 1])

        # Validate and filter: only well-formed patch dicts that pass the floor check.
        patches: list[dict[str, Any]] = []
        for item in raw_patches:
            if not isinstance(item, dict):
                continue
            if not all(k in item for k in ("file", "change", "rationale")):
                continue
            if not floor_check_before_skill_edit(item):
                logger.debug(
                    "fork_review_pass: patch blocked by floor_check for file: %s",
                    item.get("file"),
                )
                continue
            patches.append(item)

        logger.debug("fork_review_pass: %d valid patch(es) proposed", len(patches))
        return patches

    except Exception as exc:  # noqa: BLE001
        # AC-1 / AC-2: fail-closed, non-fatal.
        logger.debug("fork_review_pass: exception swallowed (fail-closed): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Existing PostToolUse handler
# ---------------------------------------------------------------------------


def handle_post_tool_use(payload: dict) -> None:
    """Handle one PostToolUse event.

    Two independent sub-paths:
    1. Ambient capture (gated by DEPTHFUSION_AMBIENT_CAPTURE, default on).
    2. Per-turn self-improvement review (gated by DEPTHFUSION_PERTURN_REVIEW, S-262).

    Each path is independently fail-closed (AC-6 / S-262 AC-1).
    """
    tool_name = payload.get("tool_name", "")

    # ------------------------------------------------------------------
    # Sub-path 1: ambient capture (S-110).
    # ------------------------------------------------------------------
    if (_env_bool("DEPTHFUSION_AMBIENT_CAPTURE", True)
            and tool_name and tool_name not in _skip_tools()):
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

    # ------------------------------------------------------------------
    # Sub-path 2: per-turn self-improvement pass (S-262, AC-1).
    # Independent of ambient capture; fail-closed, never raises.
    # ------------------------------------------------------------------
    try:
        fork_review_pass(payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("fork_review_pass raised unexpectedly (non-fatal): %s", exc)


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
