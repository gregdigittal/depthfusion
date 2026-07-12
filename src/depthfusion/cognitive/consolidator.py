"""MemoryConsolidator — find near-duplicate and stale-archive candidates.

Pinned memories are never candidates for merge or archive (security constraint).

Similarity: if an embed_fn is provided at construction time, uses cosine
similarity on embedding vectors (more semantically accurate).  Falls back to
token Jaccard when no embedder is available — preserving behaviour in
minimal/standard profiles where no LLM backend is configured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from depthfusion.core.memory_object import MemoryObject, MemoryStatus


@dataclass
class ConsolidationResult:
    merge_candidates: list[tuple[str, str]] = field(default_factory=list)
    archive_candidates: list[MemoryObject] = field(default_factory=list)


class MemoryConsolidator:
    def __init__(
        self,
        merge_threshold: float = 0.92,
        embed_fn: Optional[Callable[[list[str]], Optional[list[list[float]]]]] = None,
    ) -> None:
        self._merge_threshold = merge_threshold
        # ponytail: embed_fn is None by default — token Jaccard stays the fallback
        self._embed_fn = embed_fn

    def find_near_duplicates(self, memories: list[MemoryObject]) -> ConsolidationResult:
        result = ConsolidationResult()
        active = [
            m for m in memories
            if not m.pinned and m.status != MemoryStatus.ARCHIVED
        ]
        # Embed all active memories in one batch when an embedder is available.
        vectors: Optional[list[list[float]]] = None
        if self._embed_fn is not None and active:
            try:
                batch = self._embed_fn([m.content for m in active])
                if batch is not None and len(batch) == len(active):
                    vectors = batch
            except Exception:  # noqa: BLE001
                pass  # fall back to token similarity

        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                # S-226: memories in different project scopes must never merge
                if active[i].scope.project_id != active[j].scope.project_id:
                    continue
                if vectors is not None:
                    sim = _cosine(vectors[i], vectors[j])
                else:
                    sim = _token_similarity(active[i].content, active[j].content)
                if sim >= self._merge_threshold:
                    a, b = active[i], active[j]
                    if a.confidence.score >= b.confidence.score:
                        result.merge_candidates.append((b.id, a.id))
                    else:
                        result.merge_candidates.append((a.id, b.id))
        return result

    def find_archive_candidates(
        self,
        memories: list[MemoryObject],
        stale_days: int = 180,
    ) -> ConsolidationResult:
        result = ConsolidationResult()
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        for m in memories:
            if m.pinned:
                continue
            if m.status == MemoryStatus.STALE and m.updated_at < cutoff:
                result.archive_candidates.append(m)
        return result


def _cosine(a: Optional[list[float]], b: Optional[list[float]]) -> float:
    """Cosine similarity; falls back to 0.0 on None or zero-norm vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _token_similarity(a: str, b: str) -> float:
    """Jaccard token overlap — used when no embedder is available."""
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))
