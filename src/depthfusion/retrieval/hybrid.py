"""Recall pipeline — orchestrates BM25 + optional haiku reranker + optional ChromaDB.

PipelineMode.LOCAL:       BM25 only, no API calls
PipelineMode.VPS_TIER1:   BM25 top-10 -> HaikuReranker -> top-k
PipelineMode.VPS_TIER2:   ChromaDB top-20 + BM25 top-10 -> RRF fusion -> HaikuReranker -> top-k
"""
from __future__ import annotations

import os
from enum import Enum

from depthfusion.retrieval.reranker import HaikuReranker


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
        try:
            from depthfusion.storage.tier_manager import TierManager, Tier
            tm = TierManager()
            cfg = tm.detect_tier()
            if cfg.tier == Tier.VPS_TIER2:
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
