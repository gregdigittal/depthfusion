"""RLMClient — wrapper around the rlm package with cost tracking and ceiling enforcement."""
from __future__ import annotations

import logging
from typing import Optional

from depthfusion.core.config import DepthFusionConfig
from depthfusion.recursive.strategies import recommend_strategy
from depthfusion.recursive.trajectory import RecursiveTrajectory

logger = logging.getLogger(__name__)

_RLM_AVAILABLE: Optional[bool] = None


def _check_rlm_available() -> bool:
    global _RLM_AVAILABLE
    if _RLM_AVAILABLE is None:
        try:
            import rlm  # noqa: F401
            _RLM_AVAILABLE = True
        except ImportError:
            logger.warning("rlm package is not importable — RLMClient will stub all operations")
            _RLM_AVAILABLE = False
    return _RLM_AVAILABLE


# Rough cost estimate: ~$0.01 per 1000 tokens (conservative placeholder)
_COST_PER_TOKEN = 0.00001


def _estimate_cost(content: str) -> float:
    """Rough cost estimate based on content length."""
    approx_tokens = len(content.split())
    return approx_tokens * _COST_PER_TOKEN


class RLMClient:
    """Wrapper around the rlm package with cost tracking and ceiling enforcement.

    If rlm is not importable, logs a warning and stubs all operations.
    """

    def __init__(self, config: Optional[DepthFusionConfig] = None) -> None:
        self.config = config or DepthFusionConfig()
        self._available = _check_rlm_available()

    def is_available(self) -> bool:
        """Return True if rlm package is importable and functional."""
        return self._available

    def run(
        self,
        query: str,
        content: str,
        strategy: Optional[str] = None,
        max_cost: Optional[float] = None,
    ) -> tuple[str, RecursiveTrajectory]:
        """Run recursive LLM on content for query.

        - strategy: if None, auto-selected via recommend_strategy()
        - max_cost: if None, uses config.rlm_cost_ceiling
        - Raises ValueError if estimated cost > max_cost
        - Returns (result_text, trajectory)
        - If rlm unavailable: returns ("rlm not available", stub_trajectory)
        """
        # Auto-select strategy if not provided
        if strategy is None:
            approx_tokens = len(content.split())
            strategy = recommend_strategy(approx_tokens)

        trajectory = RecursiveTrajectory(strategy=strategy, query=query)

        if not self._available:
            trajectory.completed = True
            trajectory.error = "rlm package not available"
            return ("rlm not available", trajectory)

        # Cost ceiling check
        effective_ceiling = max_cost if max_cost is not None else self.config.rlm_cost_ceiling
        estimated = _estimate_cost(content)
        if estimated > effective_ceiling:
            msg = (
                f"Estimated cost ${estimated:.4f} exceeds"
                f" ceiling ${effective_ceiling:.4f}"
            )
            trajectory.error = msg
            raise ValueError(msg)

        try:
            import rlm as rlm_pkg

            rlm_instance = rlm_pkg.RLM(
                backend="anthropic",
                max_budget=effective_ceiling,
                max_timeout=float(self.config.rlm_timeout_seconds),
            )
            prompt = f"Query: {query}\n\nContent:\n{content}"
            completion = rlm_instance.completion(prompt)
            result_text = str(completion)

            trajectory.log_step(
                step_type=strategy,
                tokens=len(content.split()),
                cost=estimated,
                result_summary=result_text[:200],
            )
            trajectory.completed = True
            return (result_text, trajectory)

        except Exception as exc:
            trajectory.error = str(exc)
            trajectory.completed = False
            raise

