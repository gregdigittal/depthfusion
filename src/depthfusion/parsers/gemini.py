"""Google Takeout Gemini conversation parser.

Google Takeout exports Gemini conversations as a JSON file under
``Takeout/Gemini Apps Activity/``. Two observed schemas:

1. **Flat array** (most common)::

       [{"human": "user text", "model": "assistant text"}, ...]

2. **Wrapped object**::

       {"conversations": [{"human": "...", "model": "..."}]}

Both formats produce alternating ``user`` / ``assistant`` turns. Either
field may be absent in a given turn (e.g. a turn that only has a model
response); those are emitted as single-sided messages.

Timestamps are not available in the Google Takeout format; ``timestamp``
is always set to an empty string.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from depthfusion.parsers.base import ConversationMessage

logger = logging.getLogger(__name__)


def _turns_from_payload(payload: Any) -> list[dict]:
    """Normalise the payload into a flat list of turn dicts."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        inner = payload.get("conversations")
        if isinstance(inner, list):
            return inner
    return []


class GeminiParser:
    """Parse Google Takeout Gemini conversation exports."""

    def parse(self, data: str) -> list[ConversationMessage]:  # noqa: D102
        if not data or not data.strip():
            return []

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            logger.debug("GeminiParser: JSON parse failed: %s", exc)
            return []

        turns = _turns_from_payload(payload)
        if not turns:
            logger.debug("GeminiParser: no turns found in payload")
            return []

        messages: list[ConversationMessage] = []

        for turn in turns:
            if not isinstance(turn, dict):
                continue

            human_text = turn.get("human", "")
            model_text = turn.get("model", "")

            if isinstance(human_text, str) and human_text.strip():
                messages.append(
                    ConversationMessage(role="user", content=human_text.strip(), timestamp="")
                )
            if isinstance(model_text, str) and model_text.strip():
                messages.append(
                    ConversationMessage(role="assistant", content=model_text.strip(), timestamp="")
                )

        return messages


__all__ = ["GeminiParser"]
