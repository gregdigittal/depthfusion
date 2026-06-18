"""Generic / best-effort conversation parser.

Handles unknown or unrecognised conversation export formats via a
multi-stage heuristic pipeline:

1. **JSON array of ``{role, content}`` objects** — parsed directly if the
   input is valid JSON and the top-level value is a list of dicts with
   at least ``role`` and ``content`` keys.

2. **Plain-text prefix format** — scans each line for well-known speaker
   prefixes (``Human:``, ``User:``, ``Assistant:``, ``AI:``) and assigns
   roles accordingly. Continuation lines (no recognised prefix) are
   appended to the current message.

3. **Fallback** — if neither heuristic matches, the entire input is
   wrapped as a single ``assistant`` message.

Timestamps are always empty (the generic format carries no temporal
information).
"""
from __future__ import annotations

import json
import logging
import re

from depthfusion.parsers.base import ConversationMessage

logger = logging.getLogger(__name__)

# Recognised prefixes mapped to normalised roles.
_PREFIX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:Human|User)\s*:\s*", re.IGNORECASE), "user"),
    (re.compile(r"^(?:Assistant|AI|Bot|Model)\s*:\s*", re.IGNORECASE), "assistant"),
    (re.compile(r"^System\s*:\s*", re.IGNORECASE), "system"),
]


def _try_json_array(data: str) -> list[ConversationMessage] | None:
    """Attempt to parse ``data`` as a JSON array of ``{role, content}`` dicts.

    Returns a list of messages if successful, or ``None`` if the input
    is not a recognised JSON array format.
    """
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, list):
        return None

    messages: list[ConversationMessage] = []
    for item in payload:
        if not isinstance(item, dict):
            return None  # Not the expected shape — abort and try next heuristic
        role = item.get("role", "")
        content = item.get("content", "")
        if not isinstance(role, str) or not isinstance(content, str):
            return None
        if role not in ("user", "assistant", "system"):
            # Unknown role — still accept it rather than aborting
            role = "assistant"
        if content.strip():
            messages.append(ConversationMessage(role=role, content=content.strip(), timestamp=""))

    return messages


def _try_prefix_text(data: str) -> list[ConversationMessage] | None:
    """Scan plain text for speaker prefixes and segment into turns.

    Returns a list of messages if at least one known prefix was found,
    or ``None`` if the text has no recognisable structure.
    """
    lines = data.splitlines()
    messages: list[ConversationMessage] = []
    current_role: str | None = None
    current_lines: list[str] = []

    for line in lines:
        matched_role: str | None = None
        matched_content: str = line

        for pattern, role in _PREFIX_PATTERNS:
            m = pattern.match(line)
            if m:
                matched_role = role
                matched_content = line[m.end():]
                break

        if matched_role is not None:
            # Flush the previous message
            if current_role is not None:
                body = "\n".join(current_lines).strip()
                if body:
                    messages.append(
                        ConversationMessage(role=current_role, content=body, timestamp="")
                    )
            current_role = matched_role
            current_lines = [matched_content]
        else:
            if current_role is not None:
                current_lines.append(line)
            # Lines before any prefix are ignored

    # Flush the final message
    if current_role is not None:
        body = "\n".join(current_lines).strip()
        if body:
            messages.append(ConversationMessage(role=current_role, content=body, timestamp=""))

    return messages if messages else None


class GenericParser:
    """Best-effort fallback parser for unknown conversation export formats."""

    def parse(self, data: str) -> list[ConversationMessage]:  # noqa: D102
        if not data or not data.strip():
            return []

        # Stage 1: JSON array heuristic
        result = _try_json_array(data)
        if result is not None:
            return result

        # Stage 2: plain-text prefix heuristic
        result = _try_prefix_text(data)
        if result is not None:
            return result

        # Stage 3: fallback — wrap the whole blob as an assistant message
        logger.debug("GenericParser: no structure detected; wrapping entire input as assistant")
        return [ConversationMessage(role="assistant", content=data.strip(), timestamp="")]


__all__ = ["GenericParser"]
