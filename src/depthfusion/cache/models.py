"""Data models for the Intelligent Offline Cache (E-58)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EvictionPolicy(str, Enum):
    """Strategy used to select items for eviction when the cache is full."""

    LRU = "lru"
    """Least-Recently-Used: evict the item with the lowest ML score."""

    ML_SCORE = "ml_score"
    """ML-priority eviction: evict lowest `ml_score` first (default)."""


@dataclass
class CacheEntry:
    """A single cached item.

    Attributes
    ----------
    path:
        Filesystem or logical path identifying the cached resource.
    principal_id:
        The identity (user / service account) that owns this cache entry.
    last_accessed:
        Unix timestamp of the most-recent access.
    access_count:
        How many times this entry has been read from the cache.
    size_bytes:
        Unencrypted payload size in bytes.
    ml_score:
        Composite ML priority score computed by
        ``EvictionPolicy`` logic: ``access_frequency * recency_weight /
        file_size_penalty``.  Higher is *more* important.
    encrypted:
        Whether the persisted payload is Fernet-encrypted.
    data:
        Raw (unencrypted) payload bytes.  ``None`` when the entry
        is a metadata-only record (e.g. loaded from DB index without
        the payload).
    """

    path: str
    principal_id: str
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    size_bytes: int = 0
    ml_score: float = 0.0
    encrypted: bool = True
    data: Optional[bytes] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # ML score formula (E-58 S-189 AC-2)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_ml_score(
        access_count: int,
        last_accessed: float,
        size_bytes: int,
        now: Optional[float] = None,
        recency_half_life_days: float = 7.0,
    ) -> float:
        """Compute the ML priority score for an entry.

        Formula
        -------
        ::

            recency_weight  = exp(-age_days / half_life)
            file_size_penalty = log2(max(size_bytes, 1)) / 10  (≥0.1)
            score = access_frequency * recency_weight / file_size_penalty

        Parameters
        ----------
        access_count:
            Number of cache hits for this entry.
        last_accessed:
            Unix timestamp of the last access.
        size_bytes:
            Payload size; larger files score lower (penalty).
        now:
            Override "current time" for deterministic testing.
        recency_half_life_days:
            Half-life for the exponential recency decay (default 7 days).
        """
        import math

        now = now if now is not None else time.time()
        age_seconds = max(now - last_accessed, 0.0)
        age_days = age_seconds / 86_400.0
        recency_weight = math.exp(-age_days / recency_half_life_days)
        access_frequency = float(max(access_count, 0))
        # Penalty grows logarithmically; floor at 0.1 to avoid division-by-zero
        file_size_penalty = max(math.log2(max(size_bytes, 1)) / 10.0, 0.1)
        return access_frequency * recency_weight / file_size_penalty
