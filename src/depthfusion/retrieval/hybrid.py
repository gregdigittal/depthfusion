"""Recall pipeline — orchestrates BM25 + optional haiku reranker + optional ChromaDB.

PipelineMode.LOCAL:       BM25 only, no API calls
PipelineMode.VPS_TIER1:   BM25 top-10 -> HaikuReranker -> top-k
PipelineMode.VPS_TIER2:   ChromaDB top-20 + BM25 top-10 -> RRF fusion -> HaikuReranker -> top-k

v0.5.0 T-130: `apply_vector_search()` computes cosine similarity using the
embedding backend from `get_backend("embedding")` (LocalEmbeddingBackend
on vps-gpu, NullBackend elsewhere). Its output is a ranked block list
suitable for RRF fusion with BM25 results via the existing `rrf_fuse()`.
"""
from __future__ import annotations

import logging
import math
import os
from enum import Enum
from typing import Any

from depthfusion.retrieval.reranker import HaikuReranker

logger = logging.getLogger(__name__)

try:
    from depthfusion.storage.tier_manager import Tier as _StorageTier
    from depthfusion.storage.tier_manager import TierManager
    _TIER_MANAGER_AVAILABLE = True
except ImportError:
    TierManager = None  # type: ignore[assignment,misc]
    _StorageTier = None  # type: ignore[assignment]
    _TIER_MANAGER_AVAILABLE = False


class PipelineMode(Enum):
    LOCAL = "local"
    VPS_TIER1 = "vps-tier1"
    VPS_TIER2 = "vps-tier2"


class RecallPipeline:
    """Configures the retrieval pipeline based on install mode and tier."""

    def __init__(self, mode: PipelineMode = PipelineMode.LOCAL):
        self.mode = mode
        self._reranker = HaikuReranker() if mode != PipelineMode.LOCAL else None

    @classmethod
    def from_env(cls) -> "RecallPipeline":
        """Build pipeline from environment variables.

        Reads DEPTHFUSION_MODE (local|vps) and queries TierManager when in vps mode.
        Falls back to VPS_TIER1 if TierManager is unavailable (storage not yet installed).
        """
        install_mode = os.environ.get("DEPTHFUSION_MODE", "local")
        if install_mode != "vps":
            return cls(mode=PipelineMode.LOCAL)
        if not _TIER_MANAGER_AVAILABLE or TierManager is None:
            return cls(mode=PipelineMode.VPS_TIER1)
        try:
            tm = TierManager()
            cfg = tm.detect_tier()
            if _StorageTier is not None and cfg.tier == _StorageTier.VPS_TIER2:
                return cls(mode=PipelineMode.VPS_TIER2)
            return cls(mode=PipelineMode.VPS_TIER1)
        except Exception:
            return cls(mode=PipelineMode.VPS_TIER1)

    def apply_reranker(
        self, blocks: list[dict], query: str, top_k: int = 5
    ) -> list[dict]:
        """Apply the reranker if available; otherwise return top_k of BM25 order."""
        if self.mode == PipelineMode.LOCAL or self._reranker is None:
            return blocks[:top_k]
        return self._reranker.rerank(query, blocks, top_k=top_k)

    def maybe_expand_query(
        self,
        query: str,
        graph_store: "Any | None" = None,
    ) -> str:
        """Expand query with graph-linked terms when DEPTHFUSION_GRAPH_ENABLED=true.

        Returns original query unchanged if:
        - DEPTHFUSION_GRAPH_ENABLED is not 'true'
        - graph_store is None
        - graph has 0 nodes
        """
        if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() != "true":
            return query
        if graph_store is None:
            return query
        from depthfusion.graph.traverser import expand_query  # ImportError is intentionally loud
        try:
            if graph_store.node_count() == 0:
                return query
            return expand_query(query, graph_store)
        except Exception:
            return query

    def apply_vector_search(
        self,
        query: str,
        blocks: list[dict],
        *,
        top_k: int = 10,
        backend: Any = None,
    ) -> list[dict]:
        """Rank `blocks` by cosine similarity between `query` and `block['snippet']`.

        T-130: uses `get_backend("embedding")` (LocalEmbeddingBackend on
        vps-gpu mode, NullBackend elsewhere). When the backend returns
        `None` (no sentence-transformers, load failure, or NullBackend),
        this method returns an empty list — callers fuse with BM25 via
        `rrf_fuse()`, where an empty vector list is a no-op.

        Contract:
          - Requires each block to have a 'snippet' key (string content).
          - Returns a NEW list of blocks sorted by descending cos-sim.
          - Each returned block has a 'vector_score' key added.
          - Top-k is applied AFTER sorting.
          - Never raises — embedding failures return []; the pipeline
            degrades gracefully to BM25-only.
        """
        if not blocks:
            return []

        if backend is None:
            try:
                from depthfusion.backends.factory import get_backend
                backend = get_backend("embedding")
            except Exception as exc:  # noqa: BLE001
                logger.debug("apply_vector_search: backend resolution failed: %s", exc)
                return []

        # Embed query + all block snippets in a single batched call.
        snippets = [str(b.get("snippet", "")) for b in blocks]
        texts = [query] + snippets
        try:
            embeddings = backend.embed(texts)
        except Exception as exc:  # noqa: BLE001
            logger.debug("apply_vector_search: embed() raised: %s", exc)
            return []

        if embeddings is None or len(embeddings) != len(texts):
            return []

        query_vec = embeddings[0]
        block_vecs = embeddings[1:]

        scored: list[tuple[float, dict]] = []
        for block, vec in zip(blocks, block_vecs, strict=False):
            score = _cosine_similarity(query_vec, vec)
            enriched = {**block, "vector_score": score}
            scored.append((score, enriched))

        scored.sort(key=lambda t: -t[0])
        return [b for _, b in scored[:top_k]]

    def rrf_fuse(
        self,
        bm25_results: list[dict],
        vector_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """Reciprocal Rank Fusion of two ranked lists.

        Both lists must have a 'chunk_id' key. Returns deduplicated, fused list.
        RRF score = sum(1 / (k + rank)) across all lists where the doc appears.
        """
        if not vector_results:
            return bm25_results
        if not bm25_results:
            return vector_results

        scores: dict[str, float] = {}
        all_blocks: dict[str, dict] = {}

        for rank, block in enumerate(bm25_results, start=1):
            cid = block["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            all_blocks[cid] = block

        for rank, block in enumerate(vector_results, start=1):
            cid = block["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            all_blocks[cid] = block

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [all_blocks[cid] for cid, _ in ranked]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1.0, 1.0]; returns 0.0 for zero-vectors or
    length-mismatched inputs (rather than raising — the retrieval path
    must never hard-fail on degenerate embeddings).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
