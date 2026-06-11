"""Business Intelligence Layer — E-55 analytics foundation.

Exposes:
  - MetricsCollector: record usage events (search, ingest, sync) with
    principal_id + timestamp into a SQLite analytics table.
  - AggregationService: compute daily/weekly rollups from the events table.
  - analytics_router: FastAPI router mounting GET /v2/analytics/summary.
"""
from __future__ import annotations

from .aggregation import AggregationService
from .collector import AnalyticsCollector
from .router import analytics_router

__all__ = ["AnalyticsCollector", "AggregationService", "analytics_router"]
