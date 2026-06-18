"""MCP tool for model recommendation — T-717 (S-210)."""
from __future__ import annotations

import json
from typing import Any

from depthfusion.analytics.recommender import KNOWN_PROVIDERS, recommend


def recommend_model(arguments: dict) -> Any:
    """Return ranked model recommendations, applying Fable-5 vendor isolation.

    Validates ``exclude_vendors`` against the known provider enum and rejects
    unknown vendors with an error dict.
    """
    task_category = arguments.get("task_category")
    if not task_category:
        return {"error": "Missing required field: task_category"}

    exclude_vendors = arguments.get("exclude_vendors") or []
    if not isinstance(exclude_vendors, list):
        return {"error": "exclude_vendors must be a list of vendor names"}

    unknown = sorted(
        v for v in exclude_vendors if str(v).lower() not in KNOWN_PROVIDERS
    )
    if unknown:
        return {
            "error": (
                f"Unknown vendor(s) in exclude_vendors: {', '.join(unknown)}. "
                f"Known providers: {', '.join(sorted(KNOWN_PROVIDERS))}."
            )
        }

    available_models = arguments.get("available_models")
    if available_models is not None and not isinstance(available_models, list):
        return {"error": "available_models must be a list of model ids"}

    budget_usd = arguments.get("budget_usd")
    if budget_usd is not None:
        try:
            budget_usd = float(budget_usd)
        except (TypeError, ValueError):
            return {"error": "budget_usd must be a number"}
        if budget_usd < 0:
            return {"error": "budget_usd must be >= 0"}

    return recommend(
        task_category=task_category,
        context=arguments.get("context", ""),
        exclude_vendors=exclude_vendors,
        available_models=available_models,
        min_confidence=arguments.get("min_confidence"),
        budget_usd=budget_usd,
    )


def _tool_recommend_model(arguments: dict) -> str:
    """MCP dispatcher wrapper for model recommendation."""
    return json.dumps(recommend_model(arguments))
