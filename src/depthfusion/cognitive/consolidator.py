"""MemoryConsolidator — find near-duplicate and stale-archive candidates.

Pinned memories are never candidates for merge or archive (security constraint).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from depthfusion.core.memory_object import MemoryObject, MemoryStatus


@dataclass
class ConsolidationResult:
    merge_candidates: list[tuple[str, str]] = field(default_factory=list)
    archive_candidates: list[MemoryObject] = field(default_factory=list)


class MemoryConsolidator:
    def __init__(self, merge_threshold: float = 0.92) -> None:
        self._merge_threshold = merge_threshold

    def find_near_duplicates(self, memories: list[MemoryObject]) -> ConsolidationResult:
        result = ConsolidationResult()
        active = [
            m for m in memories
            if not m.pinned and m.status != MemoryStatus.ARCHIVED
        ]
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                sim = self._token_similarity(active[i].content, active[j].content)
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

    @staticmethod
    def _token_similarity(a: str, b: str) -> float:
        ta = set(a.lower().split())
        tb = set(b.lower().split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(len(ta), len(tb))
