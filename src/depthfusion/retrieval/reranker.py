"""Haiku-based semantic reranker for BM25 results (VPS Tier 1).

v0.5.0 T-120: migrated to the provider-agnostic backend interface. The
class is now a thin adapter that translates between the pipeline's
`list[dict]` block shape and the Protocol's `list[tuple[int, float]]`
rerank shape.

The `__init__` accepts an optional `backend` parameter for test injection;
production code omits it and the factory resolves the backend per the
current `DEPTHFUSION_MODE` / `DEPTHFUSION_RERANKER_BACKEND` settings.

Behaviour is semantically identical to v0.4.x when the backend is Haiku;
local-mode callers never reach this code because `RecallPipeline.apply_reranker`
short-circuits to `blocks[:top_k]` before any reranker is consulted.
"""
from __future__ import annotations

import logging
from typing import Optional

from depthfusion.backends.base import (
    BackendOverloadError,
    BackendTimeoutError,
    LLMBackend,
    RateLimitError,
)
from depthfusion.backends.factory import get_backend

logger = logging.getLogger(__name__)


class HaikuReranker:
    """Reranks BM25 result blocks using the configured reranker backend.

    Degrades gracefully to passthrough when the backend is unhealthy
    (no API key, no SDK) or when a call fails. Typed backend errors
    (rate-limit / overload / timeout) are caught at the class boundary
    and logged at debug — they do NOT propagate to the pipeline, which
    preserves v0.4.x graceful-degradation behaviour at the recall layer.
    (A future fallback-chain refactor may move this responsibility up
    to the factory; for v0.5 it stays at the call-site.)
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        backend: Optional[LLMBackend] = None,
    ) -> None:
        # `model` is retained for signature compatibility but is resolved
        # inside the backend; kept here so existing callers don't break.
        self._model = model
        self._backend: LLMBackend = backend if backend is not None else get_backend("reranker")

    def is_available(self) -> bool:
        """Return True if the reranker backend can make calls."""
        return self._backend.healthy()

    def rerank(
        self,
        query: str,
        blocks: list[dict],
        top_k: int = 3,
    ) -> list[dict]:
        """Rerank blocks by relevance to query. Returns top_k blocks.

        Falls back to original order (truncated to top_k) if the backend
        is unavailable, the call fails, or the response is unparseable.
        Preserves the v0.4.x fill-to-top_k behaviour: if the backend
        returns fewer than top_k items, remaining blocks are appended in
        their original BM25 order.
        """
        if not blocks:
            return blocks
        if not self.is_available():
            return blocks[:top_k]

        # Extract per-block text for the Protocol interface; match the
        # v0.4.x formatting (snippet or chunk_id, truncated to 300 chars).
        docs = [
            b.get("snippet", b.get("chunk_id", ""))[:300]
            for b in blocks
        ]

        try:
            idx_scores = self._backend.rerank(query, docs, top_k)
        except (RateLimitError, BackendOverloadError, BackendTimeoutError) as exc:
            logger.debug("Reranker typed-error fallback: %s", exc)
            return blocks[:top_k]
        except Exception as exc:  # noqa: BLE001 — graceful-degradation contract
            logger.debug("Reranker unexpected-error fallback: %s", exc)
            return blocks[:top_k]

        # Map indices → original blocks, preserving fill-to-top_k semantics.
        seen: set[int] = {i for i, _ in idx_scores}
        ordered: list[dict] = [blocks[i] for i, _ in idx_scores if 0 <= i < len(blocks)]
        for i, b in enumerate(blocks):
            if i not in seen and len(ordered) < top_k:
                ordered.append(b)
        return ordered[:top_k]
