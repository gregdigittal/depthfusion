"""Budget-aware model selection tests — S-211 (T-720, T-722, T-723, T-724).

Covers:
  - recommend(budget_usd=...) filters by avg_cost_usd (T-720, AC-1)
  - cheapest model returned with budget_warning when none qualify (T-720, AC-1)
  - Budget.remaining() tracking + budget_alert blocks when too poor (T-723, AC-5)
  - log_dispatch_outcome calls record_model_telemetry with real verdict/cost (T-722, AC-4)
  - GET /api/budget-summary returns spend/remaining (T-724, AC-6)
  - recommend_model MCP tool + POST /api/recommend-model pass budget_usd through
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from depthfusion.analytics.budget import (
    Budget,
    budget_alert,
    build_budget_summary,
    log_dispatch_outcome,
    min_eligible_cost,
    select_model_for_task,
)
from depthfusion.analytics.model_stats import invalidate_stats_cache
from depthfusion.analytics.recommender import recommend
from depthfusion.mcp.tools.recommender_tools import recommend_model

# ---------------------------------------------------------------------------
# Helpers — isolated telemetry DB
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Path:
    from depthfusion.telemetry import schema as tel_schema

    db_path = tmp_path / "model_telemetry.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["TELEMETRY_DB_PATH"] = str(db_path)
    tel_schema.migrate()
    return db_path


def _insert_rows(db_path: Path, rows: list[dict]) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO model_telemetry
                    (recorded_at, session_id, model_id, task_category,
                     tokens_in, tokens_out, latency_ms, cost_usd,
                     quality_verdict, project_slug)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("recorded_at", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                    row.get("session_id", "sess-1"),
                    row.get("model_id", "sonnet"),
                    row.get("task_category", "code"),
                    row.get("tokens_in", 100),
                    row.get("tokens_out", 50),
                    row.get("latency_ms", 500),
                    row.get("cost_usd"),
                    row.get("quality_verdict"),
                    row.get("project_slug"),
                ),
            )
        conn.commit()


@pytest.fixture()
def fresh_db(tmp_path):
    db_path = _make_db(tmp_path)
    invalidate_stats_cache()
    yield db_path
    os.environ.pop("TELEMETRY_DB_PATH", None)
    invalidate_stats_cache()


# ---------------------------------------------------------------------------
# T-720 — recommend(budget_usd=...) filtering
# ---------------------------------------------------------------------------

class TestRecommendBudgetFilter:
    def test_budget_filters_out_pricey_models(self, fresh_db):
        # opus pricey (0.10), sonnet cheap (0.01). Budget 0.05 → opus excluded.
        rows = []
        for _ in range(12):
            rows.append({"model_id": "opus", "quality_verdict": "pass", "cost_usd": 0.10})
            rows.append({"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.01})
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        recs = recommend(
            task_category="code",
            available_models=["opus", "sonnet"],
            budget_usd=0.05,
        )
        model_ids = {r["model_id"] for r in recs}
        assert "opus" not in model_ids
        assert "sonnet" in model_ids
        assert all(r["budget_warning"] is False for r in recs)

    def test_no_model_qualifies_returns_cheapest_with_warning(self, fresh_db):
        # All models above budget → cheapest returned, budget_warning True.
        rows = []
        for _ in range(12):
            rows.append({"model_id": "opus", "quality_verdict": "pass", "cost_usd": 0.10})
            rows.append({"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.04})
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        recs = recommend(
            task_category="code",
            available_models=["opus", "sonnet"],
            budget_usd=0.001,  # below everything
        )
        assert len(recs) == 1
        assert recs[0]["model_id"] == "sonnet"  # the cheaper of the two
        assert recs[0]["budget_warning"] is True

    def test_no_budget_means_no_warning_and_no_filter(self, fresh_db):
        rows = []
        for _ in range(12):
            rows.append({"model_id": "opus", "quality_verdict": "pass", "cost_usd": 0.10})
            rows.append({"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.01})
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        recs = recommend(task_category="code", available_models=["opus", "sonnet"])
        model_ids = {r["model_id"] for r in recs}
        assert model_ids == {"opus", "sonnet"}
        assert all(r["budget_warning"] is False for r in recs)


class TestRecommendModelToolBudget:
    def test_tool_passes_budget_through(self, fresh_db):
        rows = []
        for _ in range(12):
            rows.append({"model_id": "opus", "quality_verdict": "pass", "cost_usd": 0.10})
            rows.append({"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.01})
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        result = recommend_model(
            {
                "task_category": "code",
                "available_models": ["opus", "sonnet"],
                "budget_usd": 0.05,
            }
        )
        assert isinstance(result, list)
        assert "opus" not in {r["model_id"] for r in result}

    def test_tool_rejects_negative_budget(self, fresh_db):
        result = recommend_model({"task_category": "code", "budget_usd": -1})
        assert isinstance(result, dict)
        assert "error" in result

    def test_tool_rejects_non_numeric_budget(self, fresh_db):
        result = recommend_model({"task_category": "code", "budget_usd": "free"})
        assert isinstance(result, dict)
        assert "error" in result


# ---------------------------------------------------------------------------
# T-723 — Budget tracking + budget_alert
# ---------------------------------------------------------------------------

class TestBudgetTracking:
    def test_remaining_and_debit(self):
        b = Budget(cap_usd=5.0)
        assert b.remaining() == 5.0
        b.debit(1.25, model_id="sonnet", task="code")
        assert abs(b.remaining() - 3.75) < 1e-9
        assert b.entries[0]["model_id"] == "sonnet"

    def test_debit_clamps_negative(self):
        b = Budget(cap_usd=5.0)
        b.debit(-3.0)
        assert b.remaining() == 5.0


class TestBudgetAlert:
    def test_alert_ok_when_remaining_covers_cheapest(self, fresh_db):
        rows = [{"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.01}
                for _ in range(12)]
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        b = Budget(cap_usd=5.0)
        alert = budget_alert(b, available_models=["sonnet"])
        assert alert["ok"] is True
        assert alert["min_cost"] is not None

    def test_alert_blocks_when_too_poor(self, fresh_db):
        rows = [{"model_id": "opus", "quality_verdict": "pass", "cost_usd": 0.10}
                for _ in range(12)]
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        b = Budget(cap_usd=5.0, spent_usd=4.95)  # only 0.05 left, opus costs 0.10
        alert = budget_alert(b, available_models=["opus"])
        assert alert["ok"] is False
        assert "Budget alert" in alert["message"]

    def test_min_eligible_cost_respects_vendor_exclusion(self, fresh_db):
        rows = []
        for _ in range(12):
            rows.append({"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.01})
            rows.append({"model_id": "gpt-5", "quality_verdict": "pass", "cost_usd": 0.05})
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        # Excluding anthropic leaves only gpt-5 (0.05).
        cost = min_eligible_cost(
            exclude_vendors=["anthropic"], available_models=["sonnet", "gpt-5"]
        )
        assert abs(cost - 0.05) < 1e-9


# ---------------------------------------------------------------------------
# select_model_for_task — per-task budget = remaining / remaining_tasks
# ---------------------------------------------------------------------------

class TestSelectModelForTask:
    def test_per_task_budget_degrades_choice(self, fresh_db):
        rows = []
        for _ in range(12):
            rows.append({"model_id": "opus", "quality_verdict": "pass", "cost_usd": 0.10})
            rows.append({"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.01})
            rows.append({"model_id": "haiku", "quality_verdict": "pass", "cost_usd": 0.003})
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        # Plenty of budget → opus affordable (per-task 5/1 = 5.0). With all three
        # at equal quality the cheapest-per-dollar (haiku) ranks first, but the
        # key invariant is that opus is NOT excluded by budget here.
        b_rich = Budget(cap_usd=5.0)
        rich = select_model_for_task(
            task_category="code", budget=b_rich, remaining_tasks=1,
            available_models=["opus", "sonnet", "haiku"],
        )
        assert rich["budget_warning"] is False

        # Tight budget → per-task budget tiny, opus excluded.
        b_poor = Budget(cap_usd=0.05, spent_usd=0.0)
        poor = select_model_for_task(
            task_category="code", budget=b_poor, remaining_tasks=5,
            available_models=["opus", "sonnet", "haiku"],
        )
        # per-task budget = 0.05/5 = 0.01 → only haiku (0.003) and sonnet (0.01) qualify.
        assert poor["model_id"] in {"haiku", "sonnet"}
        assert poor["avg_cost_usd"] <= 0.01 + 1e-9


# ---------------------------------------------------------------------------
# T-722 — dispatch outcome logging closes the feedback loop
# ---------------------------------------------------------------------------

class TestDispatchOutcomeLogging:
    def test_dispatch_outcome_records_telemetry(self, fresh_db):
        b = Budget(cap_usd=5.0)
        result = log_dispatch_outcome(
            session_id="run-1",
            model_id="sonnet",
            task_category="code",
            quality_verdict="pass",
            cost_usd=0.02,
            tokens_in=100,
            tokens_out=50,
            latency_ms=400,
            project_slug="agent-ops",
            budget=b,
        )
        assert "id" in result
        assert result.get("deduplicated") in (True, False)
        # Budget debited.
        assert abs(b.remaining() - 4.98) < 1e-9
        # Telemetry row persisted with the real verdict.
        with sqlite3.connect(str(fresh_db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM model_telemetry WHERE session_id = 'run-1'"
            ).fetchone()
        assert row is not None
        assert row["quality_verdict"] == "pass"
        assert abs(row["cost_usd"] - 0.02) < 1e-9

    def test_dispatch_outcome_uses_injected_recorder(self, fresh_db):
        calls = []

        def fake_recorder(event):
            calls.append(event)
            return {"id": 999, "deduplicated": False}

        b = Budget(cap_usd=5.0)
        result = log_dispatch_outcome(
            session_id="run-2",
            model_id="opus",
            task_category="review",
            quality_verdict="fail",
            cost_usd=0.05,
            budget=b,
            recorder=fake_recorder,
        )
        assert result == {"id": 999, "deduplicated": False}
        assert calls[0]["quality_verdict"] == "fail"
        assert calls[0]["model_id"] == "opus"
        assert abs(b.remaining() - 4.95) < 1e-9

    def test_dispatch_outcome_error_does_not_debit(self, fresh_db):
        def failing_recorder(event):
            return {"error": "boom"}

        b = Budget(cap_usd=5.0)
        result = log_dispatch_outcome(
            session_id="run-3",
            model_id="sonnet",
            task_category="code",
            quality_verdict="pass",
            cost_usd=0.02,
            budget=b,
            recorder=failing_recorder,
        )
        assert "error" in result
        # No debit on failed telemetry.
        assert b.remaining() == 5.0


# ---------------------------------------------------------------------------
# T-724 — build_budget_summary + GET /api/budget-summary
# ---------------------------------------------------------------------------

class TestBudgetSummary:
    def test_summary_computes_spend_and_remaining(self, fresh_db):
        rows = [
            {"model_id": "sonnet", "cost_usd": 0.01, "quality_verdict": "pass",
             "session_id": "s1", "project_slug": "agent-ops"},
            {"model_id": "haiku", "cost_usd": 0.003, "quality_verdict": "pass",
             "session_id": "s1", "project_slug": "agent-ops"},
        ]
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        summary = build_budget_summary(
            cap_usd=5.0, project_slug="agent-ops", session_id="s1"
        )
        assert abs(summary["actual_spend_usd"] - 0.013) < 1e-9
        assert abs(summary["remaining_usd"] - (5.0 - 0.013)) < 1e-9
        assert summary["dispatch_count"] == 2
        # Per-model breakdown present.
        by_model = {b["model_id"]: b for b in summary["by_model"]}
        assert "sonnet" in by_model
        assert "haiku" in by_model

    def test_summary_endpoint_returns_spend(self, fresh_db, monkeypatch):
        monkeypatch.setenv("TELEMETRY_DB_PATH", str(fresh_db))
        _insert_rows(
            fresh_db,
            [{"model_id": "sonnet", "cost_usd": 0.02, "quality_verdict": "pass",
              "session_id": "s2", "project_slug": "agent-ops"}],
        )
        invalidate_stats_cache()

        from depthfusion.api.auth import _require_principal_dep
        from depthfusion.api.rest import app
        from depthfusion.identity.models import Principal

        fake_principal = Principal(principal_id="test-user", groups=["viewer"])
        app.dependency_overrides[_require_principal_dep] = lambda: fake_principal
        client = TestClient(app, raise_server_exceptions=True)
        try:
            resp = client.get(
                "/api/budget-summary",
                params={"project_slug": "agent-ops", "session_id": "s2", "cap_usd": 5.0},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert abs(data["actual_spend_usd"] - 0.02) < 1e-9
            assert abs(data["remaining_usd"] - 4.98) < 1e-9
        finally:
            app.dependency_overrides.clear()

    def test_summary_endpoint_requires_auth(self):
        from depthfusion.api.rest import app

        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/api/budget-summary")
        # 503 is returned when auth is not configured (CI has no OIDC/token env vars);
        # _UnconfiguredPrincipalDep intentionally returns 503 "auth_not_configured".
        assert resp.status_code in (401, 403, 422, 503)


# ---------------------------------------------------------------------------
# POST /api/recommend-model passes budget_usd through
# ---------------------------------------------------------------------------

class TestRecommendEndpointBudget:
    def test_endpoint_budget_filter(self, fresh_db, monkeypatch):
        monkeypatch.setenv("TELEMETRY_DB_PATH", str(fresh_db))
        rows = []
        for _ in range(12):
            rows.append({"model_id": "opus", "quality_verdict": "pass", "cost_usd": 0.10})
            rows.append({"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.01})
        _insert_rows(fresh_db, rows)
        invalidate_stats_cache()

        from depthfusion.api.auth import _require_principal_dep
        from depthfusion.api.rest import app
        from depthfusion.identity.models import Principal

        fake_principal = Principal(principal_id="test-user", groups=["viewer"])
        app.dependency_overrides[_require_principal_dep] = lambda: fake_principal
        client = TestClient(app, raise_server_exceptions=True)
        try:
            resp = client.post(
                "/api/recommend-model",
                json={
                    "task_category": "code",
                    "available_models": ["opus", "sonnet"],
                    "budget_usd": 0.05,
                },
            )
            assert resp.status_code == 200
            model_ids = {r["model_id"] for r in resp.json()}
            assert "opus" not in model_ids
            assert "sonnet" in model_ids
        finally:
            app.dependency_overrides.clear()
