"""InstallRecommender — converts check_all() results into actionable install steps."""
from __future__ import annotations

from depthfusion.analyzer.compatibility import GREEN, RED, YELLOW


class InstallRecommender:
    """Converts compatibility check results to actionable installation steps."""

    def recommend(self, check_results: dict) -> list[dict]:
        """Convert check_all() results to actionable steps.

        Returns list of {action, detail, priority}.
        Priority: "critical" (RED), "recommended" (YELLOW), "optional" (GREEN).
        """
        steps: list[dict] = []
        for constraint_id, result in check_results.items():
            status = result.get("status", GREEN)
            if status == RED:
                steps.append(
                    {
                        "action": f"Fix {constraint_id}: {result['message']}",
                        "detail": result.get("detail", ""),
                        "priority": "critical",
                    }
                )
            elif status == YELLOW:
                steps.append(
                    {
                        "action": f"Review {constraint_id}: {result['message']}",
                        "detail": result.get("detail", ""),
                        "priority": "recommended",
                    }
                )
        return steps
