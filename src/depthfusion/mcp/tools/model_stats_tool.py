"""MCP tool for learned model performance statistics (S-209)."""
from __future__ import annotations

import json
from typing import Any

from depthfusion.analytics.model_stats import get_model_stats


def query_model_performance(arguments: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return learned model performance stats as ``{"stats": [...]}``."""
    window_days = int(arguments.get("window_days", 30))
    return {
        "stats": get_model_stats(
            model_id=arguments.get("model_id"),
            task_category=arguments.get("task_category"),
            window_days=window_days,
        )
    }


def _tool_query_model_performance(arguments: dict[str, Any]) -> str:
    """MCP dispatcher wrapper for ``query_model_performance``."""
    return json.dumps(query_model_performance(arguments))
