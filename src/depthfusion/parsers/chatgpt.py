"""ChatGPT conversation export parser.

Parses the ``conversations.json`` file produced by ChatGPT's data-export
feature (Settings → Data controls → Export data).

Export format (array of conversation objects)::

    [
      {
        "title": "My conversation",
        "mapping": {
          "<uuid>": {
            "message": {
              "author": {"role": "user" | "assistant" | "tool" | "system"},
              "content": {"parts": ["text fragment", ...]},
              "create_time": 1700000000.123   # Unix timestamp; may be null
            }
          },
          ...
        }
      },
      ...
    ]

Only ``"user"`` and ``"assistant"`` roles are extracted. Messages with no
text content (empty ``parts``, null message nodes) are silently skipped.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from depthfusion.parsers.base import ConversationMessage

logger = logging.getLogger(__name__)


def _unix_to_iso(ts: Any) -> str:
    """Convert a Unix timestamp (float/int) to an ISO-8601 string, or "" on failure."""
    try:
        return datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc).isoformat()
    except Exception:
        return ""


def _extract_text(parts: Any) -> str:
    """Concatenate text fragments from a ``parts`` list."""
    if not isinstance(parts, list):
        return ""
    return "".join(p for p in parts if isinstance(p, str)).strip()


class ChatGPTParser:
    """Parse ChatGPT ``conversations.json`` exports."""

    def parse(self, data: str) -> list[ConversationMessage]:  # noqa: D102
        if not data or not data.strip():
            return []

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            logger.debug("ChatGPTParser: JSON parse failed: %s", exc)
            return []

        if not isinstance(payload, list):
            logger.debug("ChatGPTParser: expected top-level list, got %s", type(payload).__name__)
            return []

        messages: list[ConversationMessage] = []

        for conversation in payload:
            if not isinstance(conversation, dict):
                continue
            mapping = conversation.get("mapping")
            if not isinstance(mapping, dict):
                continue

            for node in mapping.values():
                if not isinstance(node, dict):
                    continue
                msg = node.get("message")
                if not isinstance(msg, dict):
                    continue

                author = msg.get("author", {})
                if not isinstance(author, dict):
                    continue
                role = author.get("role", "")
                if role not in ("user", "assistant"):
                    continue

                content_obj = msg.get("content", {})
                parts = content_obj.get("parts", []) if isinstance(content_obj, dict) else []
                text = _extract_text(parts)
                if not text:
                    continue

                timestamp = _unix_to_iso(msg.get("create_time"))
                messages.append(ConversationMessage(role=role, content=text, timestamp=timestamp))

        return messages


__all__ = ["ChatGPTParser"]
