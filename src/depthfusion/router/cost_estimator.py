"""Cost estimator — rough token counting and USD cost estimation.

v0.5.1 T-167 / S-54: extended with `budget_tokens_for_ceiling()` for
API-side task-budget enforcement when the Anthropic SDK supports it.
Bridges the RLM's USD cost ceiling to the token budget the API expects.
"""
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

    def budget_tokens_for_ceiling(
        self, ceiling_usd: float, model: str = "opus",
    ) -> int:
        """Translate a USD cost ceiling into a token budget for the model.

        This is the bridge that lets the RLM pre-declare "don't spend more
        than $X" as an API-side budget, instead of estimating consumed
        cost post-hoc from usage telemetry.

        **Important caveat for operators:** the budget is computed against
        **input pricing**, which is the CHEAPEST rate for each model.
        Under output-heavy responses, the actual cost can exceed the USD
        ceiling by up to the output/input price ratio (5× for opus,
        5.1× for sonnet, 5× for haiku). Example: $0.50 ceiling on opus
        yields 33 333 tokens; if all are output tokens, actual spend is
        33 333 × $0.075/1000 = $2.50 (5× the ceiling).

        Rationale for using input pricing anyway: the task-budgets beta
        surface is the API's own enforcement channel, and the API itself
        bills at per-token rates that don't match a blended USD/token
        conversion. Using input pricing gives the *maximum permissible*
        token count — the most generous translation, so queries aren't
        blocked short of the ceiling under input-heavy traffic. Operators
        who need strict USD bounds under worst-case output should set
        `DEPTHFUSION_RLM_COST_CEILING` to 1/5th of their true budget,
        or rely on `max_budget` (the rlm-level USD check) instead of
        leaning on the task-budget header alone.

        Floor semantics ensure the returned integer never exceeds the
        rational value: `int()` truncates toward zero.

        Returns:
            Integer token budget. Always ≥ 0. Zero when ceiling is ≤ 0
            (caller is saying "don't run the query at all").

        Raises:
            KeyError: when `model` is not in PRICING.
        """
        if ceiling_usd <= 0.0:
            return 0
        rates = PRICING[model]  # raises KeyError for unknown model
        # ceiling_usd / (input_rate_per_1k_tokens / 1000) = budget_tokens
        # int() floor: returned value never exceeds the rational.
        budget_tokens = int((ceiling_usd / rates["input"]) * 1000)
        return budget_tokens
