"""Tests for the Business Intelligence Layer (E-55).

Covers:
  - AnalyticsCollector: record_event writes to DB; count_events returns correct counts
  - AggregationService: daily/weekly rollup computation; summary endpoint data
  - ACL enforcement: principal can only retrieve their own metrics (not another's)
  - REST endpoint: GET /v2/analytics/summary — auth, period parsing, response shape
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from depthfusion.analytics.aggregation import AggregationService
from depthfusion.analytics.collector import AnalyticsCollector
from depthfusion.analytics.router import analytics_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "analytics.db"


@pytest.fixture()
def collector(db_path: Path) -> AnalyticsCollector:
    return AnalyticsCollector(db_path=db_path)


@pytest.fixture()
def aggregation(db_path: Path) -> AggregationService:
    return AggregationService(db_path=db_path)


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    """TestClient wired to an app that uses a temp DB path."""
    from fastapi import Header as _Header

    app = FastAPI()
    app.include_router(analytics_router)

    # Override the DB path dependency to use our temp path
    from depthfusion.analytics.router import _default_db_path, _resolve_principal_id
    app.dependency_overrides[_default_db_path] = lambda: db_path

    # Override principal resolution: extract the bearer token and use it
    # directly as the principal_id.  This replaces the OIDC JWT validation
    # that is not available in unit tests, while keeping the 401 behaviour
    # for missing / empty tokens (so auth-failure tests still work).
    async def _test_resolve_principal(
        authorization: str | None = _Header(default=None),
    ) -> str:
        from fastapi import HTTPException, status as _status

        if not authorization:
            raise HTTPException(
                status_code=_status.HTTP_401_UNAUTHORIZED,
                detail={"error": "missing_token", "detail": "Authorization header required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=_status.HTTP_401_UNAUTHORIZED,
                detail={"error": "invalid_token", "detail": "Bearer token required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(
                status_code=_status.HTTP_401_UNAUTHORIZED,
                detail={"error": "missing_token", "detail": "Empty bearer token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return token

    app.dependency_overrides[_resolve_principal_id] = _test_resolve_principal

    return TestClient(app)


# ---------------------------------------------------------------------------
# AnalyticsCollector tests
# ---------------------------------------------------------------------------

class TestAnalyticsCollector:
    def test_record_event_and_count(self, collector: AnalyticsCollector) -> None:
        """record_event stores events that count_events can retrieve."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-1", event_type="ingest", recorded_at=now)

        assert collector.count_events(
            principal_id="user-1", event_type="search", since=now - timedelta(hours=1)
        ) == 2
        assert collector.count_events(
            principal_id="user-1", event_type="ingest", since=now - timedelta(hours=1)
        ) == 1

    def test_record_event_unknown_type(self, collector: AnalyticsCollector) -> None:
        """Unknown event types are stored verbatim without error."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-1", event_type="custom_op", recorded_at=now)
        assert collector.count_events(
            principal_id="user-1", event_type="custom_op", since=now - timedelta(hours=1)
        ) == 1

    def test_count_events_respects_since(self, collector: AnalyticsCollector) -> None:
        """count_events only counts events at or after the since boundary."""
        old = datetime(2024, 1, 1, tzinfo=timezone.utc)
        recent = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-1", event_type="sync", recorded_at=old)
        collector.record_event(principal_id="user-1", event_type="sync", recorded_at=recent)

        # Only the recent one should be counted
        assert collector.count_events(
            principal_id="user-1", event_type="sync", since=recent - timedelta(hours=1)
        ) == 1

    def test_count_events_isolated_by_principal(self, collector: AnalyticsCollector) -> None:
        """count_events returns 0 for principals with no events."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-A", event_type="search", recorded_at=now)

        assert collector.count_events(
            principal_id="user-B", event_type="search", since=now - timedelta(hours=1)
        ) == 0

    def test_recent_events_returns_dicts(self, collector: AnalyticsCollector) -> None:
        """recent_events returns a list of dicts with expected keys."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        events = collector.recent_events(principal_id="user-1", since=now - timedelta(hours=1))

        assert len(events) == 1
        assert events[0]["principal_id"] == "user-1"
        assert events[0]["event_type"] == "search"
        assert "recorded_at" in events[0]

    def test_recent_events_filter_by_type(self, collector: AnalyticsCollector) -> None:
        """recent_events event_type filter limits results."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-1", event_type="ingest", recorded_at=now)

        search_events = collector.recent_events(
            principal_id="user-1", since=now - timedelta(hours=1), event_type="search"
        )
        assert len(search_events) == 1
        assert search_events[0]["event_type"] == "search"


# ---------------------------------------------------------------------------
# AggregationService tests
# ---------------------------------------------------------------------------

class TestAggregationService:
    def test_summary_empty_db(self, aggregation: AggregationService) -> None:
        """Summary on an empty DB returns zero counts with correct shape."""
        result = aggregation.summary(principal_id="user-1", period_days=7)
        assert result["principal_id"] == "user-1"
        assert result["period_days"] == 7
        assert result["total_events"] == 0
        assert result["by_event_type"] == {}
        assert "period_start" in result
        assert "period_end" in result

    def test_summary_counts_events_in_window(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """Summary counts only events within the period window."""
        now = datetime.now(tz=timezone.utc)
        # Event within window
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-1", event_type="ingest", recorded_at=now)
        # Event outside window (30 days ago, but period=7d)
        old = now - timedelta(days=30)
        collector.record_event(principal_id="user-1", event_type="sync", recorded_at=old)

        result = aggregation.summary(principal_id="user-1", period_days=7)
        assert result["total_events"] == 3
        assert result["by_event_type"]["search"] == 2
        assert result["by_event_type"]["ingest"] == 1
        assert "sync" not in result["by_event_type"]

    def test_summary_acl_principal_isolation(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """Summary only returns metrics for the requested principal."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-A", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-A", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-B", event_type="search", recorded_at=now)

        result_a = aggregation.summary(principal_id="user-A", period_days=7)
        result_b = aggregation.summary(principal_id="user-B", period_days=7)

        assert result_a["total_events"] == 2
        assert result_b["total_events"] == 1

    def test_compute_rollups_writes_rows(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """compute_rollups returns > 0 rows when events exist."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-1", event_type="ingest", recorded_at=now)

        rows_written = aggregation.compute_rollups()
        assert rows_written > 0

    def test_compute_rollups_idempotent(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """Running compute_rollups twice does not raise or duplicate rows."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)

        first = aggregation.compute_rollups()
        second = aggregation.compute_rollups()
        # Both runs should succeed and write the same number of rows (INSERT OR REPLACE)
        assert first == second

    def test_summary_period_1d(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """Summary with period_days=1 only includes today's events."""
        now = datetime.now(tz=timezone.utc)
        yesterday = now - timedelta(days=2)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-1", event_type="search", recorded_at=yesterday)

        result = aggregation.summary(principal_id="user-1", period_days=1)
        # Only today's event should be counted
        assert result["total_events"] == 1


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------

class TestAnalyticsSummaryEndpoint:
    def test_get_summary_returns_200(
        self, client: TestClient, collector: AnalyticsCollector
    ) -> None:
        """GET /v2/analytics/summary returns 200 with valid auth."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="test-principal", event_type="search", recorded_at=now)

        resp = client.get(
            "/v2/analytics/summary?period=7d",
            headers={"Authorization": "Bearer test-principal"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["principal_id"] == "test-principal"
        assert body["period_days"] == 7
        assert body["total_events"] == 1
        assert body["by_event_type"]["search"] == 1

    def test_get_summary_no_auth_returns_401(self, client: TestClient) -> None:
        """GET /v2/analytics/summary without auth returns 401."""
        resp = client.get("/v2/analytics/summary?period=7d")
        assert resp.status_code == 401

    def test_get_summary_empty_bearer_returns_401(self, client: TestClient) -> None:
        """Empty Bearer token returns 401."""
        resp = client.get(
            "/v2/analytics/summary?period=7d",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    def test_get_summary_acl_isolation(
        self, client: TestClient, collector: AnalyticsCollector
    ) -> None:
        """Principal A cannot see principal B's metrics."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="user-A", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-A", event_type="search", recorded_at=now)
        collector.record_event(principal_id="user-B", event_type="ingest", recorded_at=now)

        resp_a = client.get(
            "/v2/analytics/summary?period=7d",
            headers={"Authorization": "Bearer user-A"},
        )
        resp_b = client.get(
            "/v2/analytics/summary?period=7d",
            headers={"Authorization": "Bearer user-B"},
        )

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["total_events"] == 2
        assert resp_b.json()["total_events"] == 1
        # user-A cannot see user-B's ingest
        assert "ingest" not in resp_a.json()["by_event_type"]

    def test_get_summary_default_period_is_7d(
        self, client: TestClient
    ) -> None:
        """Omitting period defaults to 7d."""
        resp = client.get(
            "/v2/analytics/summary",
            headers={"Authorization": "Bearer test-user"},
        )
        assert resp.status_code == 200
        assert resp.json()["period_days"] == 7

    def test_get_summary_custom_period(
        self, client: TestClient, collector: AnalyticsCollector
    ) -> None:
        """period=30d returns a 30-day window."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="p-30d", event_type="sync", recorded_at=now)

        resp = client.get(
            "/v2/analytics/summary?period=30d",
            headers={"Authorization": "Bearer p-30d"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["period_days"] == 30
        assert body["total_events"] == 1

    def test_get_summary_response_shape(self, client: TestClient) -> None:
        """Response body has all required fields."""
        resp = client.get(
            "/v2/analytics/summary?period=7d",
            headers={"Authorization": "Bearer shape-test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        required_keys = {
            "principal_id", "period_days", "period_start",
            "period_end", "total_events", "by_event_type",
        }
        assert required_keys.issubset(body.keys())
