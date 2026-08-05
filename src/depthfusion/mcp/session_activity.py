"""Per-session activity tracker (S-250 / E-73, T-849).

``mcp/http_server.py``'s ``_publish_session_checkpoint`` (shared by the
``DELETE /mcp`` handler and the FastAPI lifespan shutdown hook) needs real
``plan_state`` / ``files_modified`` / ``context_pct_at_checkpoint`` data —
before this module existed those three fields were hardcoded placeholders
(``""`` / ``[]`` / ``None``), which is the literal S-250 AC-1 gap tracked at
``BACKLOG.md`` (S-250).

This module is the missing signal source: ``mcp/server.py::_process_request``
calls :func:`record_tool_call` on every ``tools/call`` request, and
``http_server.py`` calls :func:`snapshot_for_checkpoint` when it is about to
publish a checkpoint for that session.

Design notes
------------
* **Process-local, in-memory, best-effort.** This is not a durable store —
  it does not need to be. A server restart is exactly the moment the
  lifespan shutdown hook flushes whatever was tracked into a durable
  ``CheckpointRecord`` (which *is* persisted, via ``EventStore``); there is
  nothing left to recover after that flush completes.
* **No fabrication.** When a session has made no tracked tool calls,
  :func:`snapshot_for_checkpoint` returns the exact same placeholder triple
  (``""``, ``[]``, ``None``) the pre-T-849 code hardcoded unconditionally —
  that remains correct in the genuine no-activity case. Only sessions with
  observed activity get non-placeholder values.
* **Generic extraction, not per-tool coupling.** File-path and
  context-percentage signals are read directly off the raw MCP
  ``arguments`` dict for *any* tool call (not just tools that are "about"
  files) — this keeps the tracker decoupled from individual tool
  implementations and avoids growing a per-tool special-case list here.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

# Argument keys (checked in order) that may carry file-path signal on any
# tool call's arguments dict. Not tool-specific — any tool's caller may pass
# these; whichever key(s) are present are unioned into files_modified.
_FILE_ARG_KEYS = ("files_modified", "source_files", "files", "file_path")

# Argument keys that may carry a context-window utilisation signal.
_CONTEXT_PCT_KEYS = ("context_pct_at_checkpoint", "context_pct")

# Rolling window cap — keeps plan_state summaries bounded and this tracker's
# memory footprint bounded for long-lived sessions (performance.md: avoid
# unbounded growth).
_MAX_TRACKED_TOOL_CALLS = 50
_PLAN_STATE_RECENT_N = 5


@dataclass
class SessionActivity:
    """Mutable, in-memory activity record for a single MCP session."""

    tool_calls: list[str] = field(default_factory=list)
    files_modified: set[str] = field(default_factory=set)
    context_pct: float | None = None

    def record(self, tool_name: str, arguments: dict) -> None:
        """Fold one tool call's signal into this session's activity record."""
        self.tool_calls.append(tool_name)
        if len(self.tool_calls) > _MAX_TRACKED_TOOL_CALLS:
            # Rolling window — drop the oldest rather than growing unbounded.
            del self.tool_calls[: len(self.tool_calls) - _MAX_TRACKED_TOOL_CALLS]

        for key in _FILE_ARG_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value:
                self.files_modified.add(value)
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, str) and item:
                        self.files_modified.add(item)

        for key in _CONTEXT_PCT_KEYS:
            value = arguments.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.context_pct = float(value)
                break

    def plan_state_summary(self) -> str:
        """A compact, human-readable summary of this session's activity.

        Real data derived from observed tool calls — not a fabricated plan
        description. Intentionally terse: this fills a `CheckpointRecord`
        field, not a full transcript.
        """
        if not self.tool_calls:
            return ""
        total = len(self.tool_calls)
        recent = self.tool_calls[-_PLAN_STATE_RECENT_N:]
        return f"{total} tool call(s) this session; most recent: {', '.join(recent)}"


_LOCK = threading.Lock()
_SESSIONS: dict[str, SessionActivity] = {}


def record_tool_call(session_id: str | None, tool_name: str, arguments: dict | None) -> None:
    """Record one tool-call activity event for *session_id*.

    A falsy ``session_id`` (e.g. the stdio transport, which has no
    per-connection session concept) or a falsy ``tool_name`` is a no-op —
    there is nothing meaningful to key a tracked record on.
    """
    if not session_id or not tool_name:
        return
    with _LOCK:
        activity = _SESSIONS.setdefault(session_id, SessionActivity())
        activity.record(tool_name, arguments or {})


def get_activity(session_id: str | None) -> SessionActivity | None:
    """Return the tracked :class:`SessionActivity` for *session_id*, if any."""
    if not session_id:
        return None
    with _LOCK:
        return _SESSIONS.get(session_id)


def snapshot_for_checkpoint(
    session_id: str | None,
) -> tuple[str, list[str], float | None]:
    """Return ``(plan_state, files_modified, context_pct)`` for a checkpoint publish.

    Returns the placeholder triple (``""``, ``[]``, ``None``) when there is
    no tracked activity for this session (including when ``session_id`` is
    falsy) — this is the correct value when activity genuinely did not
    occur, not a bug.
    """
    activity = get_activity(session_id)
    if activity is None:
        return "", [], None
    return (
        activity.plan_state_summary(),
        sorted(activity.files_modified),
        activity.context_pct,
    )


def clear_session(session_id: str | None) -> None:
    """Drop tracked activity for *session_id*.

    Called once a checkpoint has been published for that session (clean
    close via ``DELETE /mcp``, or a shutdown sweep) so a subsequent session
    reusing the same id — unlikely, but not impossible — starts fresh.
    """
    if not session_id:
        return
    with _LOCK:
        _SESSIONS.pop(session_id, None)
