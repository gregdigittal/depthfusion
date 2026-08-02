"""Business Intelligence Layer — E-55 analytics foundation.

Exposes:
  - MetricsCollector: record usage events (search, ingest, sync) with
    principal_id + timestamp into a SQLite analytics table.
  - AggregationService: compute daily/weekly rollups from the events table.
  - analytics_router: FastAPI router mounting GET /v2/analytics/summary. *(lazy)*
"""
from __future__ import annotations

from .aggregation import AggregationService
from .collector import AnalyticsCollector

__all__ = ["AnalyticsCollector", "AggregationService", "analytics_router"]

# analytics_router imports fastapi — not available in the base install.
# Defer until first access so the MCP stdio path works without extras.
_LAZY: dict[str, str] = {
    "analytics_router": ".router",
}


def __getattr__(name: str) -> object:  # noqa: ANN401
    submodule = _LAZY.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(submodule, package=__name__)
    obj = getattr(mod, name)
    globals()[name] = obj
    return obj
