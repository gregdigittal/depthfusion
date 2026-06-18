"""Base types for conversation parsers.

Every provider-specific parser implements the `ConversationParser` Protocol,
which normalises an export blob (raw string) into a list of
`ConversationMessage` dataclasses with a common ``role / content / timestamp``
structure.

Roles are standardised to "user", "assistant", or "system". Provider-specific
roles (e.g. ChatGPT's "tool") are silently dropped by each parser.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ConversationMessage:
    """A single turn in a normalised conversation.

    Attributes:
        role:      "user", "assistant", or "system".
        content:   The text body of the message.
        timestamp: ISO-8601 string when available; empty string otherwise.
    """

    role: str
    content: str
    timestamp: str  # ISO-8601 or "" if unavailable


@runtime_checkable
class ConversationParser(Protocol):
    """Provider-agnostic conversation-export parser contract."""

    def parse(self, data: str) -> list[ConversationMessage]:
        """Normalise a raw export string into a list of ConversationMessages.

        Implementations MUST:
          - Return an empty list on empty input or malformed data.
          - Never raise; swallow parse errors and return what could be decoded.
          - Skip messages whose role is not "user" or "assistant" (provider
            roles such as "tool", "function", "system" should be dropped unless
            the caller explicitly handles "system" turns).
        """
        ...


__all__ = ["ConversationMessage", "ConversationParser"]
