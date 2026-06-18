"""DeepSeek conversation export parser.

Two export schemas are handled:

1. **Wrapped object** (DeepSeek web export)::

       {
         "conversations": [
           {
             "messages": [
               {"role": "user", "content": "..."},
               {"role": "assistant", "content": "..."}
             ]
           }
         ]
       }

2. **Flat array** (API history / some export variants)::

       [{"role": "user", "content": "..."}, ...]

Only ``"user"`` and ``"assistant"`` roles are extracted. System messages
are silently dropped.

Timestamps are not present in known DeepSeek export formats; ``timestamp``
is always set to an empty string.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from depthfusion.parsers.base import ConversationMessage

logger = logging.getLogger(__name__)


def _flatten_messages(payload: Any) -> list[dict]:
    """Return a flat list of message dicts regardless of export schema."""
    if isinstance(payload, list):
        # Could be a flat messages array or an array of conversation objects.
        if payload and isinstance(payload[0], dict) and "messages" in payload[0]:
            # Array of conversation objects — concatenate all message lists.
            result: list[dict] = []
            for conv in payload:
                if isinstance(conv, dict):
                    msgs = conv.get("messages", [])
                    if isinstance(msgs, list):
                        result.extend(msgs)
            return result
        # Assume it's already a flat list of message objects.
        return payload

    if isinstance(payload, dict):
        conversations = payload.get("conversations")
        if isinstance(conversations, list):
            result = []
            for conv in conversations:
                if isinstance(conv, dict):
                    msgs = conv.get("messages", [])
                    if isinstance(msgs, list):
                        result.extend(msgs)
            return result

    return []


class DeepSeekParser:
    """Parse DeepSeek conversation exports (both wrapped and flat formats)."""

    def parse(self, data: str) -> list[ConversationMessage]:  # noqa: D102
        if not data or not data.strip():
            return []

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            logger.debug("DeepSeekParser: JSON parse failed: %s", exc)
            return []

        raw_messages = _flatten_messages(payload)
        if not raw_messages:
            logger.debug("DeepSeekParser: no messages found in payload")
            return []

        messages: list[ConversationMessage] = []

        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            messages.append(ConversationMessage(role=role, content=content.strip(), timestamp=""))

        return messages


__all__ = ["DeepSeekParser"]
