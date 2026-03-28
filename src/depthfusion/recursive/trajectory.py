"""RecursiveTrajectory — log of a recursive LLM execution for observability."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RecursiveTrajectory:
    """Log of a recursive LLM execution for observability."""

    strategy: str
    query: str
    sub_calls: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    quality_score: Optional[float] = None
    completed: bool = False
    error: Optional[str] = None
    steps: list[dict] = field(default_factory=list)

    def log_step(
        self,
        step_type: str,
        tokens: int,
        cost: float,
        result_summary: str,
    ) -> None:
        """Append a step to the trajectory."""
        self.steps.append(
            {
                "step_type": step_type,
                "tokens": tokens,
                "cost": cost,
                "result_summary": result_summary,
            }
        )
        self.total_tokens += tokens
        self.estimated_cost += cost
        self.sub_calls += 1
