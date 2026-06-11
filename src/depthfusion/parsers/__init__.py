"""depthfusion.parsers — multi-provider conversation export normaliser.

Supported providers:
  - ``"chatgpt"``  — ChatGPT data-export ``conversations.json``
  - ``"gemini"``   — Google Takeout Gemini export
  - ``"deepseek"`` — DeepSeek conversation export (wrapped or flat)
  - ``"generic"``  — best-effort fallback for unknown formats

Public API::

    from depthfusion.parsers import parse_conversation

    messages = parse_conversation("chatgpt", raw_json_string)
    # → [{"role": "user", "content": "...", "timestamp": "..."}, ...]
"""
from __future__ import annotations

from depthfusion.parsers.base import ConversationMessage, ConversationParser
from depthfusion.parsers.chatgpt import ChatGPTParser
from depthfusion.parsers.deepseek import DeepSeekParser
from depthfusion.parsers.gemini import GeminiParser
from depthfusion.parsers.generic import GenericParser

_PARSERS: dict[str, ConversationParser] = {
    "chatgpt": ChatGPTParser(),
    "gemini": GeminiParser(),
    "deepseek": DeepSeekParser(),
    "generic": GenericParser(),
}


def parse_conversation(provider: str, data: str) -> list[dict]:
    """Parse a conversation export into a list of normalised message dicts.

    Args:
        provider: One of ``"chatgpt"``, ``"gemini"``, ``"deepseek"``, or
                  ``"generic"``. Unknown values fall back to the generic parser.
        data:     Raw export string (typically JSON).

    Returns:
        A list of dicts with keys ``"role"``, ``"content"``, and
        ``"timestamp"`` (ISO-8601 string or empty string).
    """
    parser = _PARSERS.get(provider, _PARSERS["generic"])
    messages = parser.parse(data)
    return [{"role": m.role, "content": m.content, "timestamp": m.timestamp} for m in messages]


from depthfusion.parsers.documents.base import (
    QuarantineEntry,
    QuarantineStore,
    get_quarantine,
    get_quarantine_store,
    quarantine,
)

__all__ = [
    "ChatGPTParser",
    "ConversationMessage",
    "ConversationParser",
    "DeepSeekParser",
    "GenericParser",
    "GeminiParser",
    "parse_conversation",
    # Document quarantine (T-590 / T-591)
    "QuarantineEntry",
    "QuarantineStore",
    "get_quarantine",
    "get_quarantine_store",
    "quarantine",
]
