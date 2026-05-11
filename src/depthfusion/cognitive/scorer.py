from __future__ import annotations

from dataclasses import dataclass

_WEIGHTS = {
    "semantic": 0.25,
    "lexical": 0.18,
    "confidence": 0.15,
    "regime_match": 0.12,
    "graph_proximity": 0.10,
    "recency": 0.08,
    "historical_usefulness": 0.07,
    "workflow_intent": 0.05,
}


@dataclass
class ScoringContext:
    semantic: float = 0.0
    lexical: float = 0.0
    confidence: float = 0.7
    regime_match: float = 0.0
    graph_proximity: float = 0.0
    recency: float = 0.5
    historical_usefulness: float = 0.0
    workflow_intent: float = 0.0


class CognitiveScorer:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights = weights if weights is not None else dict(_WEIGHTS)
        total = sum(self._weights.values())
        assert abs(total - 1.0) < 1e-6, f"Weights must sum to 1.0, got {total}"

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def score(self, ctx: ScoringContext) -> float:
        score, _ = self.score_with_breakdown(ctx)
        return score

    def score_with_breakdown(self, ctx: ScoringContext) -> tuple[float, dict[str, float]]:
        components = {
            "semantic": ctx.semantic,
            "lexical": ctx.lexical,
            "confidence": ctx.confidence,
            "regime_match": ctx.regime_match,
            "graph_proximity": ctx.graph_proximity,
            "recency": ctx.recency,
            "historical_usefulness": ctx.historical_usefulness,
            "workflow_intent": ctx.workflow_intent,
        }
        breakdown = {k: v * self._weights[k] for k, v in components.items()}
        total = sum(breakdown.values())
        return min(1.0, max(0.0, total)), breakdown
