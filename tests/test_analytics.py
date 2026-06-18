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
def client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient wired to an app that uses a temp DB path."""
    # Enable the unauthenticated dev fallback so tests can pass a raw Bearer
    # token without a full OIDC stack.  The resolver reads this at request
    # time so setting it here (after module import) is sufficient.
    monkeypatch.setenv("DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS", "1")

    app = FastAPI()
    app.include_router(analytics_router)

    # Override the DB path dependency to use our temp path
    from depthfusion.analytics.router import _default_db_path
    app.dependency_overrides[_default_db_path] = lambda: db_path

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


# ---------------------------------------------------------------------------
# Facet endpoint tests (T-622)
# ---------------------------------------------------------------------------

class TestAnalyticsFacets:
    def test_facets_groups_by_event_type(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """facets() returns per-event_type buckets scoped to the principal."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="f-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="f-1", event_type="search", recorded_at=now)
        collector.record_event(principal_id="f-1", event_type="ingest", recorded_at=now)

        result = aggregation.facets(principal_id="f-1", facet="event_type", period_days=7)
        assert result["facet"] == "event_type"
        assert result["total"] == 3
        assert result["buckets"]["search"] == 2
        assert result["buckets"]["ingest"] == 1

    def test_facets_rejects_unsupported_facet(
        self, aggregation: AggregationService
    ) -> None:
        """A facet not on the allowlist (e.g. principal_id) raises ValueError."""
        with pytest.raises(ValueError):
            aggregation.facets(principal_id="f-1", facet="principal_id")

    def test_facets_acl_isolation(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """facets() never crosses principals."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="f-A", event_type="search", recorded_at=now)
        collector.record_event(principal_id="f-B", event_type="search", recorded_at=now)
        result_a = aggregation.facets(principal_id="f-A")
        assert result_a["total"] == 1

    def test_get_facets_endpoint(
        self, client: TestClient, collector: AnalyticsCollector
    ) -> None:
        """GET /v2/analytics/facets returns 200 with grouped buckets."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="fe-1", event_type="sync", recorded_at=now)
        resp = client.get(
            "/v2/analytics/facets?facet=event_type&period=7d",
            headers={"Authorization": "Bearer fe-1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["facet"] == "event_type"
        assert body["buckets"]["sync"] == 1

    def test_get_facets_unsupported_facet_returns_422(
        self, client: TestClient
    ) -> None:
        """Requesting an unsupported facet yields 422, not a 500."""
        resp = client.get(
            "/v2/analytics/facets?facet=secret&period=7d",
            headers={"Authorization": "Bearer fe-x"},
        )
        assert resp.status_code == 422

    def test_get_facets_no_auth_returns_401(self, client: TestClient) -> None:
        """Facet endpoint requires auth."""
        resp = client.get("/v2/analytics/facets?facet=event_type")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Facet performance pass (T-622) — index existence + SLO
# ---------------------------------------------------------------------------

class TestFacetPerformance:
    def test_composite_facet_index_exists(self, db_path: Path) -> None:
        """The T-622 composite group-by index is created by init_db."""
        import sqlite3

        from depthfusion.analytics.store import init_db

        init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        assert "idx_ae_facet_principal_recorded_type" in names

    def test_facet_query_uses_index_not_scan(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """EXPLAIN QUERY PLAN selects the composite index, not a full scan."""
        now = datetime.now(tz=timezone.utc)
        collector.record_event(principal_id="probe", event_type="search", recorded_at=now)
        plan = " ".join(aggregation.explain_facet_query(principal_id="probe"))
        # The planner must use the facet index — never a full table scan.
        assert "idx_ae_facet_principal_recorded_type" in plan
        assert "SCAN analytics_events" not in plan or "USING INDEX" in plan

    def test_facet_query_meets_slo(
        self, collector: AnalyticsCollector, aggregation: AggregationService
    ) -> None:
        """A facet query over a populated table meets the p95 < 500ms SLO.

        Mirrors the SLO pattern in tests/test_performance.py: run the facet
        query repeatedly and assert the 95th-percentile wall-clock time is
        comfortably under the generous 500ms budget.
        """
        import time

        now = datetime.now(tz=timezone.utc)
        # Populate a few principals with a spread of event types.
        for i in range(2000):
            collector.record_event(
                principal_id=f"p-{i % 20}",
                event_type=("search", "ingest", "sync")[i % 3],
                recorded_at=now - timedelta(days=i % 7),
            )

        timings: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            aggregation.facets(principal_id="p-3", facet="event_type", period_days=7)
            t1 = time.perf_counter()
            timings.append((t1 - t0) * 1000)

        timings.sort()
        p95_ms = timings[int(len(timings) * 0.95) - 1]
        assert p95_ms < 500.0, (
            f"Facet query p95 latency {p95_ms:.1f}ms exceeds 500ms SLO."
        )
