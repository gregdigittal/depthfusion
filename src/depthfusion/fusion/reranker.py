"""Pluggable reranker interface for DepthFusion.

Provides a ``Reranker`` Protocol so callers can swap in any implementation
(passthrough, LLM-based, cross-encoder, etc.) without changing the fusion
pipeline's API.

Current implementations:
- ``PassthroughReranker``: identity — returns chunks unchanged.
- ``LLMReranker``: stub — logs a warning and falls back to passthrough.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from depthfusion.core.types import RetrievedChunk

logger = logging.getLogger(__name__)


@runtime_checkable
class Reranker(Protocol):
    """Protocol for all reranker implementations."""

    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Rerank chunks for the given query.

        Args:
            query:  The user's query string.
            chunks: Candidate chunks to rerank.

        Returns:
            Reranked list of chunks (same objects, potentially different order).
        """
        ...


class PassthroughReranker:
    """Identity reranker — returns chunks unchanged. Default."""

    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Return chunks in the same order without modification."""
        return chunks


class LLMReranker:
    """Calls an LLM to rerank chunks.

    Stub implementation — logs a warning and falls back to passthrough until
    a concrete LLM integration is wired in.

    Args:
        model: Model tier hint (e.g. "haiku", "sonnet", "opus").
    """

    def __init__(self, model: str = "haiku") -> None:
        self._model = model

    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Rerank using an LLM. Currently not yet implemented — uses passthrough."""
        logger.warning(
            "LLMReranker not yet implemented, using passthrough "
            "(model=%s, query=%r, n_chunks=%d)",
            self._model,
            query,
            len(chunks),
        )
        return chunks
