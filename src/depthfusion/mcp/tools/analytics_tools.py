"""Compatibility imports for model performance analytics tools."""
from __future__ import annotations

from depthfusion.mcp.tools.model_stats_tool import (
    _tool_query_model_performance,
    query_model_performance,
)

__all__ = ["_tool_query_model_performance", "query_model_performance"]
