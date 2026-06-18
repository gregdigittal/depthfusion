"""MCP tools for model telemetry capture."""
from __future__ import annotations

import json

from depthfusion.telemetry.recorder import QUALITY_VERDICTS, TASK_CATEGORIES, record_event

REQUIRED_FIELDS = frozenset(
    {
        "session_id",
        "model_id",
        "task_category",
        "tokens_in",
        "tokens_out",
        "latency_ms",
        "cost_usd",
    }
)


def record_model_telemetry(arguments: dict) -> dict:
    """Record one model telemetry event and return its row id."""
    missing = sorted(field for field in REQUIRED_FIELDS if field not in arguments)
    if missing:
        return {"error": f"Missing required telemetry fields: {', '.join(missing)}"}

    task_category = arguments.get("task_category")
    if task_category not in TASK_CATEGORIES:
        return {"error": f"Invalid task_category: {task_category!r}"}

    quality_verdict = arguments.get("quality_verdict")
    if quality_verdict is not None and quality_verdict not in QUALITY_VERDICTS:
        return {"error": f"Invalid quality_verdict: {quality_verdict!r}"}

    try:
        return record_event(arguments)
    except ValueError as exc:
        return {"error": str(exc)}


def _tool_record_model_telemetry(arguments: dict) -> str:
    """MCP dispatcher wrapper for model telemetry capture."""
    return json.dumps(record_model_telemetry(arguments))
