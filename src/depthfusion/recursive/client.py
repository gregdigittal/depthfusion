"""RLMClient — wrapper around the rlm package with cost tracking and ceiling enforcement.

v0.5.1 T-166 / S-54: opt-in Opus 4.7 task-budget enforcement. When the
Anthropic SDK supports the task-budget beta, the RLM passes a token
budget (translated from the USD cost ceiling) to the API so the
enforcement happens server-side rather than post-hoc. When the SDK
lacks support, falls back to the pre-v0.5 post-hoc estimation path
with a DEBUG log — behaviour is byte-identical to v0.4.x for operators
who haven't upgraded the SDK.

Per build plan §TG-13 kill-criterion, this is shipped as a "best effort
wrapper without CIQS claim" — the feature guards against budget
overshoots when the beta is stable, and is a no-op on older SDKs.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from depthfusion.core.config import DepthFusionConfig
from depthfusion.recursive.strategies import recommend_strategy
from depthfusion.recursive.trajectory import RecursiveTrajectory
from depthfusion.router.cost_estimator import CostEstimator

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


def _task_budget_beta_available() -> bool:
    """Probe whether the Anthropic SDK supports the task-budgets beta.

    Two gates, both must pass:
      1. `DEPTHFUSION_RLM_TASK_BUDGET_ENABLED` env var is truthy — lets
         operators opt out even if the SDK reports support.
      2. The SDK exposes a `task_budget` attribute (or any of the
         documented future entry points). Currently the beta is not in
         any shipped SDK release, so this gate returns False by default.
         When Anthropic ships the beta, adjust the attribute probe to
         match the shipped surface without requiring callers to change.

    Returns False silently on any import or attribute-probe failure —
    the fallback path must never raise.
    """
    raw = os.environ.get("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", "").strip().lower()
    if raw not in ("true", "1", "yes"):
        return False
    try:
        import anthropic
        # Probe: the beta is expected to surface as either a module-level
        # `task_budget` attribute or a feature enum on `anthropic.types`.
        # Neither is present in the 0.x SDKs shipped at v0.5.1 tag time.
        if hasattr(anthropic, "task_budget"):
            return True
        types_mod = getattr(anthropic, "types", None)
        if types_mod is not None and hasattr(types_mod, "TaskBudget"):
            return True
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        logger.debug("task-budget probe failed: %s", exc)
    return False


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

            # S-54: when the task-budget beta is available, translate the
            # USD ceiling to a token budget and pass it to the RLM. When
            # rlm supports a `task_budget_tokens` kwarg (it doesn't yet),
            # this goes through directly; otherwise `rlm_kwargs` is an
            # empty dict and behaviour is identical to pre-v0.5.
            rlm_kwargs: dict = {
                "backend": "anthropic",
                "max_budget": effective_ceiling,
                "max_timeout": float(self.config.rlm_timeout_seconds),
            }
            if _task_budget_beta_available():
                estimator = CostEstimator()
                token_budget = estimator.budget_tokens_for_ceiling(
                    effective_ceiling, model="opus",
                )
                # Probe the rlm signature — not all rlm versions accept
                # a `task_budget_tokens` kwarg. inspect.signature fails
                # closed: if we can't confirm support, skip the kwarg.
                try:
                    import inspect
                    rlm_sig = inspect.signature(rlm_pkg.RLM.__init__)
                    if "task_budget_tokens" in rlm_sig.parameters:
                        rlm_kwargs["task_budget_tokens"] = token_budget
                        logger.info(
                            "RLM task-budget enabled: %d tokens (ceiling $%.4f, opus)",
                            token_budget, effective_ceiling,
                        )
                    else:
                        logger.debug(
                            "task-budget beta available but rlm package does not "
                            "accept task_budget_tokens kwarg; post-hoc estimation only",
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("rlm signature probe failed: %s", exc)
            else:
                logger.debug(
                    "task-budget beta not available; using post-hoc cost estimation",
                )

            rlm_instance = rlm_pkg.RLM(**rlm_kwargs)
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

