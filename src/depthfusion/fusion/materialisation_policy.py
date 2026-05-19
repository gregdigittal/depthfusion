"""MaterialisationPolicy — Selective persistence for fusion results.

Maps to Mamba's Δ gate at the storage layer: decides which fused results
are worth persisting to long-term memory vs. being discarded after use.

Three gates:
  1. Score threshold — minimum fused score to be considered for persistence
  2. Novelty gate — how different is this result from what's already stored
  3. Capacity management — evict lowest-value items when storage is full

Port of materialisation-policy.ts (SkillForge depthfusion-core).
Zero DepthFusion internal imports — standalone fusion primitive.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_DEFAULT_SCORE_THRESHOLD = 0.1
_DEFAULT_NOVELTY_THRESHOLD = 0.2
_DEFAULT_MAX_CAPACITY = 500


@dataclass(frozen=True)
class MaterialisableItem:
    id: str
    score: float
    content: str
    embedding: list[float] | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MaterialisationDecision:
    item_id: str
    score: float
    passed_score_threshold: bool
    novelty_score: float
    passed_novelty_gate: bool
    materialised: bool
    reason: str


@dataclass(frozen=True)
class MaterialisationResult:
    """Result of applying the materialisation policy to a batch."""

    accepted: list[MaterialisableItem]
    rejected: list[MaterialisableItem]
    decisions: list[MaterialisationDecision]
    evicted: list[MaterialisableItem]


class MaterialisationPolicy:
    """Evaluate a batch of fusion results and decide which to materialise.

    Maintains an in-memory store across calls. Load/save the store via
    load_store() / get_store() for persistent-storage integration.
    """

    def __init__(
        self,
        *,
        score_threshold: float = _DEFAULT_SCORE_THRESHOLD,
        novelty_threshold: float = _DEFAULT_NOVELTY_THRESHOLD,
        max_capacity: int = _DEFAULT_MAX_CAPACITY,
    ) -> None:
        self._score_threshold = score_threshold
        self._novelty_threshold = novelty_threshold
        self._max_capacity = max_capacity
        self._store: list[MaterialisableItem] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, items: list[MaterialisableItem]) -> MaterialisationResult:
        """Evaluate items through the three-gate pipeline.

        Gates (in order):
          1. score >= score_threshold
          2. novelty(item, store) >= novelty_threshold
          3. capacity management (evict lowest-score items if over cap)
        """
        accepted: list[MaterialisableItem] = []
        rejected: list[MaterialisableItem] = []
        decisions: list[MaterialisationDecision] = []
        evicted: list[MaterialisableItem] = []

        for item in items:
            # Gate 1: score threshold
            passed_score = item.score >= self._score_threshold
            if not passed_score:
                decisions.append(
                    MaterialisationDecision(
                        item_id=item.id,
                        score=item.score,
                        passed_score_threshold=False,
                        novelty_score=0.0,
                        passed_novelty_gate=False,
                        materialised=False,
                        reason=(
                            f"Score {item.score:.3f} below threshold "
                            f"{self._score_threshold}"
                        ),
                    )
                )
                rejected.append(item)
                continue

            # Gate 2: novelty
            novelty_score = self._compute_novelty(item)
            passed_novelty = novelty_score >= self._novelty_threshold
            if not passed_novelty:
                decisions.append(
                    MaterialisationDecision(
                        item_id=item.id,
                        score=item.score,
                        passed_score_threshold=True,
                        novelty_score=novelty_score,
                        passed_novelty_gate=False,
                        materialised=False,
                        reason=(
                            f"Novelty {novelty_score:.3f} below threshold "
                            f"{self._novelty_threshold} — too similar to existing item"
                        ),
                    )
                )
                rejected.append(item)
                continue

            # Passed both gates — include
            decisions.append(
                MaterialisationDecision(
                    item_id=item.id,
                    score=item.score,
                    passed_score_threshold=True,
                    novelty_score=novelty_score,
                    passed_novelty_gate=True,
                    materialised=True,
                    reason="Accepted: passed score and novelty gates",
                )
            )
            accepted.append(item)

        # Gate 3: capacity management
        self._store.extend(accepted)
        if len(self._store) > self._max_capacity:
            self._store.sort(key=lambda x: x.score)
            excess = len(self._store) - self._max_capacity
            evicted.extend(self._store[:excess])
            self._store = self._store[excess:]

        return MaterialisationResult(
            accepted=accepted,
            rejected=rejected,
            decisions=decisions,
            evicted=evicted,
        )

    def get_store(self) -> list[MaterialisableItem]:
        return list(self._store)

    @property
    def size(self) -> int:
        return len(self._store)

    def load_store(self, items: list[MaterialisableItem]) -> None:
        self._store = list(items)

    def clear(self) -> None:
        self._store = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_novelty(self, item: MaterialisableItem) -> float:
        """1 − max_cosine_similarity to any stored item.

        Returns 1.0 when the store is empty (maximally novel).
        Falls back to ID-based deduplication when embeddings are unavailable.
        """
        if not self._store:
            return 1.0

        if item.embedding:
            max_sim = -math.inf
            for stored in self._store:
                if stored.embedding and len(stored.embedding) == len(item.embedding):
                    sim = _cosine(item.embedding, stored.embedding)
                    if sim > max_sim:
                        max_sim = sim
            if max_sim > -math.inf:
                return 1.0 - max_sim

        # Fallback: exact ID match → zero novelty
        for stored in self._store:
            if stored.id == item.id:
                return 0.0
        return 1.0


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(ai * bi for ai, bi in zip(a, b))
    norm_a = math.sqrt(sum(ai * ai for ai in a))
    norm_b = math.sqrt(sum(bi * bi for bi in b))
    denom = norm_a * norm_b
    return dot / denom if denom != 0.0 else 0.0
