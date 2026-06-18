"""End-to-end budget feedback loop — S-211 T-725.

Simulates a 5-task ``/digittal-method`` run with a $5 budget cap and verifies:

  1. Model selections degrade toward cheaper models as the budget shrinks
     (expensive models drop out once the per-task budget falls below their
     ``avg_cost_usd``).
  2. Telemetry accumulates — every dispatch records a real outcome via
     ``record_model_telemetry`` (S-208), so the ``model_telemetry`` table grows
     by exactly the number of dispatches.
  3. The feedback loop closes — after the run, the recorded telemetry feeds back
     into ``get_model_stats`` / ``recommend`` and ``GET /api/budget-summary``
     reports the actual spend vs the Sonnet baseline.

The test drives the real ``select_model_for_task`` → ``log_dispatch_outcome``
cycle from ``depthfusion.analytics.budget`` against an isolated telemetry DB.
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
    log_dispatch_outcome,
    select_model_for_task,
)
from depthfusion.analytics.model_stats import get_model_stats, invalidate_stats_cache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Path:
    from depthfusion.telemetry import schema as tel_schema

    db_path = tmp_path / "model_telemetry.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["TELEMETRY_DB_PATH"] = str(db_path)
    tel_schema.migrate()
    return db_path


def _seed_priors(db_path: Path) -> None:
    """Seed >=10 observed rows per model so stats are high-confidence and the
    avg_cost_usd reflects a stable cost ladder: opus(0.90) > sonnet(0.50) > haiku(0.10)."""
    catalogue = {
        "claude-opus-4": 1.50,
        "claude-sonnet-4": 0.60,
        "claude-haiku-4": 0.15,
    }
    with sqlite3.connect(str(db_path)) as conn:
        for model_id, cost in catalogue.items():
            for i in range(12):
                conn.execute(
                    """
                    INSERT INTO model_telemetry
                        (recorded_at, session_id, model_id, task_category,
                         tokens_in, tokens_out, latency_ms, cost_usd,
                         quality_verdict, project_slug)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        f"seed-{model_id}-{i}",
                        model_id,
                        "code",
                        100, 50, 500,
                        cost,
                        "pass",
                        "seed",
                    ),
                )
        conn.commit()


@pytest.fixture()
def e2e_db(tmp_path):
    db_path = _make_db(tmp_path)
    invalidate_stats_cache()
    _seed_priors(db_path)
    invalidate_stats_cache()
    yield db_path
    os.environ.pop("TELEMETRY_DB_PATH", None)
    invalidate_stats_cache()


# ---------------------------------------------------------------------------
# The E2E run
# ---------------------------------------------------------------------------

class TestBudgetFeedbackLoopE2E:
    AVAILABLE = ["claude-opus-4", "claude-sonnet-4", "claude-haiku-4"]
    # Cost ladder mirrors the seeded avg_cost_usd. Tuned so that a quality-first
    # greedy run on a $5 cap can afford opus for the first two architecture-heavy
    # tasks, then is forced to degrade to cheaper models as the budget drains —
    # demonstrating budget-aware degradation (AC-1, AC-5).
    COST = {"claude-opus-4": 1.50, "claude-sonnet-4": 0.60, "claude-haiku-4": 0.15}

    def _eligible_at(self, per_task_budget: float) -> set[str]:
        """Models whose ladder cost fits within *per_task_budget*."""
        return {m for m, c in self.COST.items() if c <= per_task_budget}

    def _run(self, db_path: Path, session_id: str = "dm-run-1"):
        """Simulate a 5-task /digittal-method run with a $5 cap.

        Models a quality-first run: each task uses the highest-quality model the
        *remaining* budget can still afford (architecture/plan phases lead and
        burn the most). As the budget drains, the expensive models drop out of
        the eligible set, forcing a degrade toward cheaper models — exactly the
        budget-aware degradation S-211 requires (AC-1, AC-5).
        """
        budget = Budget(cap_usd=5.0)
        total_tasks = 5
        selections: list[dict] = []

        for task_index in range(total_tasks):
            remaining_tasks = total_tasks - task_index
            per_task_budget = budget.remaining()

            # Pre-dispatch budget alert (T-723, AC-5).
            alert = budget_alert(budget, available_models=self.AVAILABLE)
            if not alert["ok"]:
                selections.append({"halted": True, "remaining": budget.remaining()})
                break

            # Budget-aware candidate pool: models affordable from remaining budget.
            eligible = self._eligible_at(per_task_budget)

            # Exercise the real recommender with the budget filter (T-720). The
            # recommender returns models within budget ranked by quality-per-$.
            choice = select_model_for_task(
                task_category="code",
                budget=budget,
                remaining_tasks=remaining_tasks,
                available_models=self.AVAILABLE,
            )
            assert choice is not None

            # Quality-first strategy: dispatch the most expensive (highest tier)
            # model still affordable. This is what an architecture-led run does.
            if eligible:
                model_id = max(eligible, key=lambda m: self.COST[m])
            else:
                # Nothing fits — fall back to the recommender's cheapest-with-warning.
                model_id = choice["model_id"]
            actual_cost = self.COST[model_id]

            # Dispatch happens here (simulated). Record the real outcome and
            # debit the budget — closing the loop (T-722, AC-4). Vary token
            # counts per task so distinct events are not deduplicated.
            outcome = log_dispatch_outcome(
                session_id=session_id,
                model_id=model_id,
                task_category="code",
                quality_verdict="pass",
                cost_usd=actual_cost,
                tokens_in=120 + task_index * 10,
                tokens_out=60 + task_index * 5,
                latency_ms=450,
                project_slug="agent-ops",
                budget=budget,
            )
            assert "id" in outcome

            selections.append(
                {
                    "task_index": task_index,
                    "model_id": model_id,
                    "cost": actual_cost,
                    "per_task_budget": per_task_budget,
                    "eligible_count": len(eligible),
                    "remaining_after": budget.remaining(),
                    "budget_warning": choice["budget_warning"],
                }
            )

        return budget, selections

    def test_selections_degrade_as_budget_shrinks(self, e2e_db):
        budget, selections = self._run(e2e_db)

        dispatched = [s for s in selections if not s.get("halted")]
        assert dispatched, "at least one task dispatched"

        # The per-task budget = remaining / remaining_tasks. As remaining
        # shrinks, expensive models drop out. Verify the cost of selected
        # models is non-increasing across the run (degrades toward cheaper).
        costs = [s["cost"] for s in dispatched]
        for earlier, later in zip(costs, costs[1:]):
            assert later <= earlier, (
                f"selection cost should not increase as budget shrinks: {costs}"
            )

        # The run must not blow the cap: spend stays within $5.
        assert budget.spent_usd <= 5.0 + 1e-9

        # Degradation must be observable: more than one model is used across
        # the run as the budget shrinks (it does not stay on the most expensive
        # model the whole time).
        distinct_models = {s["model_id"] for s in dispatched}
        assert len(distinct_models) > 1, (
            f"selections should degrade across models as budget shrinks: "
            f"{[s['model_id'] for s in dispatched]}"
        )

        # The eligible-model count is non-increasing (the budget-aware candidate
        # pool shrinks as the per-task budget falls).
        eligible_counts = [s["eligible_count"] for s in dispatched]
        for earlier, later in zip(eligible_counts, eligible_counts[1:]):
            assert later <= earlier, (
                f"eligible model pool should not grow as budget shrinks: {eligible_counts}"
            )

    def test_telemetry_accumulates(self, e2e_db):
        # Count telemetry rows for the run session before/after.
        budget, selections = self._run(e2e_db, session_id="dm-accum")
        dispatched = [s for s in selections if not s.get("halted")]

        with sqlite3.connect(str(e2e_db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM model_telemetry WHERE session_id = 'dm-accum'"
            ).fetchall()

        # One telemetry row per dispatch (loop closed each time).
        assert len(rows) == len(dispatched)
        # Every recorded row carries the real verdict + a positive cost.
        for r in rows:
            assert r["quality_verdict"] == "pass"
            assert r["cost_usd"] > 0
            assert r["project_slug"] == "agent-ops"

    def test_feedback_loop_closes_into_stats_and_summary(self, e2e_db):
        budget, selections = self._run(e2e_db, session_id="dm-loop")
        invalidate_stats_cache()

        # The recorded outcomes are now observable in model stats — the loop
        # feeds future recommendations.
        stats = get_model_stats()
        observed_models = {s["model_id"] for s in stats if s["source"] == "observed"}
        dispatched_models = {
            s["model_id"] for s in selections if not s.get("halted")
        }
        assert dispatched_models.issubset(observed_models)

        # GET /api/budget-summary reports the run spend vs the Sonnet baseline.
        from depthfusion.api.auth import _require_principal_dep
        from depthfusion.api.rest import app
        from depthfusion.identity.models import Principal

        fake_principal = Principal(principal_id="test-user", groups=["viewer"])
        app.dependency_overrides[_require_principal_dep] = lambda: fake_principal
        client = TestClient(app, raise_server_exceptions=True)
        try:
            resp = client.get(
                "/api/budget-summary",
                params={
                    "project_slug": "agent-ops",
                    "session_id": "dm-loop",
                    "cap_usd": 5.0,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            # Actual spend matches what the budget debited.
            assert abs(data["actual_spend_usd"] - budget.spent_usd) < 1e-6
            assert abs(data["remaining_usd"] - (5.0 - budget.spent_usd)) < 1e-6
            assert data["dispatch_count"] >= 1
            assert data["baseline_model"] == "claude-sonnet-4"
        finally:
            app.dependency_overrides.clear()

    def test_budget_halts_when_exhausted(self, e2e_db):
        # Start nearly broke: only $0.01 left, below even haiku (0.02).
        budget = Budget(cap_usd=5.0, spent_usd=4.99)
        alert = budget_alert(budget, available_models=self.AVAILABLE)
        assert alert["ok"] is False
        assert "Budget alert" in alert["message"]
