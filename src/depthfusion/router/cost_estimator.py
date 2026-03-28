"""Cost estimator — rough token counting and USD cost estimation."""
from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    "haiku":  {"input": 0.00025, "output": 0.00125},   # per 1K tokens
    "sonnet": {"input": 0.003,   "output": 0.015},
    "opus":   {"input": 0.015,   "output": 0.075},
}


class CostEstimator:
    """Rough token counting and USD cost estimation for DepthFusion queries."""

    def estimate_tokens(self, text: str) -> int:
        """Rough token count: len(text) // 4."""
        return len(text) // 4

    def estimate_cost(self, input_text: str, model: str = "haiku") -> float:
        """Estimate USD cost for processing input_text with the given model.

        Uses input pricing only (output tokens unknown at query time).
        Raises KeyError if model is not in PRICING.
        """
        rates = PRICING[model]  # raises KeyError for unknown model
        tokens = self.estimate_tokens(input_text)
        cost = rates["input"] * (tokens / 1000)
        return cost

    def exceeds_ceiling(self, input_text: str, model: str, ceiling: float) -> bool:
        """Return True if estimated cost strictly exceeds ceiling (not equal)."""
        cost = self.estimate_cost(input_text, model)
        return cost > ceiling
