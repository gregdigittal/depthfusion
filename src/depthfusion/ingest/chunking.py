"""Chunking strategies for the document ingestion framework (E-53).

Two strategies are provided:

* :class:`FixedSizeChunker` — splits text into roughly equal token-sized
  windows with a configurable overlap.
* :class:`SentenceBoundaryChunker` — splits at sentence boundaries so
  chunks stay semantically coherent.

Both implement the :class:`ChunkingStrategy` protocol.
"""
from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

# Rough approximation: 1 token ≈ 4 characters (GPT-style tokenisation).
_CHARS_PER_TOKEN: int = 4

# Regex for sentence boundaries: period / exclamation / question followed
# by whitespace or end-of-string.
_SENTENCE_END_RE: re.Pattern[str] = re.compile(r"(?<=[.!?])\s+")


@runtime_checkable
class ChunkingStrategy(Protocol):
    """Protocol for text chunkers.

    A chunker takes a plain-text string and returns a list of non-overlapping
    (or overlapping, for sliding-window strategies) string chunks.
    """

    def chunk(self, text: str) -> list[str]:
        """Split *text* into chunks.

        Args:
            text: Input plain text.

        Returns:
            An ordered list of text chunks.  May be empty if *text* is empty.
        """
        ...


class FixedSizeChunker:
    """Sliding-window chunker with configurable token size and overlap.

    Chunks are produced by advancing a window of *chunk_tokens* tokens
    over the text, stepping forward by ``chunk_tokens - overlap_tokens``
    tokens between chunks.

    Args:
        chunk_tokens:   Approximate size of each chunk in tokens.
        overlap_tokens: How many tokens to repeat at the start of each
                        subsequent chunk for context continuity.
    """

    def __init__(
        self,
        chunk_tokens: int = 1000,
        overlap_tokens: int = 200,
    ) -> None:
        if overlap_tokens >= chunk_tokens:
            raise ValueError(
                f"overlap_tokens ({overlap_tokens}) must be less than "
                f"chunk_tokens ({chunk_tokens})"
            )
        self._chunk_chars = chunk_tokens * _CHARS_PER_TOKEN
        self._overlap_chars = overlap_tokens * _CHARS_PER_TOKEN
        self._step_chars = self._chunk_chars - self._overlap_chars

    def chunk(self, text: str) -> list[str]:
        """Split *text* into fixed-size windows with overlap."""
        if not text:
            return []

        chunks: list[str] = []
        start = 0
        length = len(text)

        while start < length:
            end = min(start + self._chunk_chars, length)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= length:
                break
            start += self._step_chars

        return chunks


class SentenceBoundaryChunker:
    """Chunker that respects sentence boundaries.

    Accumulates sentences until adding the next one would exceed
    *max_tokens*, then starts a new chunk.  Individual sentences longer
    than *max_tokens* are hard-split at the character limit.

    Args:
        max_tokens: Soft maximum number of tokens per chunk.
    """

    def __init__(self, max_tokens: int = 1000) -> None:
        self._max_chars = max_tokens * _CHARS_PER_TOKEN

    def chunk(self, text: str) -> list[str]:
        """Split *text* at sentence boundaries."""
        if not text:
            return []

        sentences = _SENTENCE_END_RE.split(text)
        chunks: list[str] = []
        current_parts: list[str] = []
        current_len: int = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # Hard-split a sentence that is itself longer than the limit.
            while len(sentence) > self._max_chars:
                head = sentence[: self._max_chars]
                if current_parts:
                    chunks.append(" ".join(current_parts))
                    current_parts = []
                    current_len = 0
                chunks.append(head)
                sentence = sentence[self._max_chars :]

            if not sentence:
                continue

            if current_len + len(sentence) + 1 > self._max_chars and current_parts:
                chunks.append(" ".join(current_parts))
                current_parts = []
                current_len = 0

            current_parts.append(sentence)
            current_len += len(sentence) + 1

        if current_parts:
            chunks.append(" ".join(current_parts))

        return chunks


__all__ = ["ChunkingStrategy", "FixedSizeChunker", "SentenceBoundaryChunker"]
