"""Integration tests for the model recommendation engine — T-719 (S-210).

Covers:
  - vendor-exclusion filtering (Fable-5 isolation, AC-3)
  - prior blending engages at n < 10 (AC-5)
  - ranking order by quality_rate / cost_per_pass with avg_cost tie-break (AC-4)
  - recommend_model MCP tool rejects unknown vendors (T-717)
  - POST /api/recommend-model returns the ranked recommendation (T-718, AC-7)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from depthfusion.analytics.model_stats import invalidate_stats_cache
from depthfusion.analytics.recommender import recommend, vendor_for_model
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
                    row.get("recorded_at", "2026-01-01T00:00:00Z"),
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


# ---------------------------------------------------------------------------
# Vendor mapping sanity
# ---------------------------------------------------------------------------

class TestVendorMapping:
    def test_anthropic_short_and_canonical_names(self):
        assert vendor_for_model("opus") == "anthropic"
        assert vendor_for_model("sonnet") == "anthropic"
        assert vendor_for_model("haiku") == "anthropic"
        assert vendor_for_model("claude-opus-4") == "anthropic"

    def test_other_vendors(self):
        assert vendor_for_model("gpt-5") == "openai"
        assert vendor_for_model("deepseek-v4") == "deepseek"
        assert vendor_for_model("gemini-2") == "google"


# ---------------------------------------------------------------------------
# Vendor exclusion (Fable-5 isolation, AC-3)
# ---------------------------------------------------------------------------

class TestVendorExclusion:
    @pytest.fixture(autouse=True)
    def _fresh_db(self, tmp_path):
        self.db_path = _make_db(tmp_path)
        invalidate_stats_cache()
        yield
        os.environ.pop("TELEMETRY_DB_PATH", None)
        invalidate_stats_cache()

    def test_excluding_anthropic_removes_all_anthropic_models(self):
        # Observed Anthropic + non-Anthropic models.
        _insert_rows(
            self.db_path,
            [{"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.012}
             for _ in range(12)]
            + [{"model_id": "gpt-5", "quality_verdict": "pass", "cost_usd": 0.02}
               for _ in range(12)],
        )
        recs = recommend(
            task_category="review",
            exclude_vendors=["anthropic"],
            available_models=["sonnet", "opus", "haiku", "gpt-5"],
        )
        providers = {r["provider"] for r in recs}
        model_ids = {r["model_id"] for r in recs}
        assert "anthropic" not in providers
        assert "sonnet" not in model_ids
        assert "opus" not in model_ids
        assert "haiku" not in model_ids
        # The non-anthropic model survives.
        assert "gpt-5" in model_ids

    def test_no_exclusion_keeps_anthropic(self):
        _insert_rows(
            self.db_path,
            [{"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.012}
             for _ in range(12)],
        )
        recs = recommend(
            task_category="review",
            exclude_vendors=[],
            available_models=["sonnet", "gpt-5"],
        )
        assert "sonnet" in {r["model_id"] for r in recs}


# ---------------------------------------------------------------------------
# Prior blending at n < 10 (AC-5)
# ---------------------------------------------------------------------------

class TestPriorBlending:
    @pytest.fixture(autouse=True)
    def _fresh_db(self, tmp_path):
        self.db_path = _make_db(tmp_path)
        invalidate_stats_cache()
        yield
        os.environ.pop("TELEMETRY_DB_PATH", None)
        invalidate_stats_cache()

    def test_low_sample_count_is_low_confidence_not_rejected(self):
        # Only 3 observed rows for sonnet -> confidence "low", priors blended.
        _insert_rows(
            self.db_path,
            [{"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.012}
             for _ in range(3)],
        )
        recs = recommend(
            task_category="code",
            available_models=["sonnet"],
        )
        assert len(recs) == 1
        rec = recs[0]
        # Low-confidence model is surfaced, not dropped.
        assert rec["model_id"] == "sonnet"
        assert rec["confidence"] == "low"
        assert rec["sample_count"] == 3

    def test_model_with_no_data_uses_prior(self):
        # No telemetry at all; opus prior should drive the recommendation.
        recs = recommend(
            task_category="planning",
            available_models=["opus"],
        )
        assert len(recs) == 1
        rec = recs[0]
        assert rec["model_id"] == "opus"
        assert rec["source"] == "prior"
        assert rec["confidence"] == "low"
        # Prior quality_rate for opus is 0.92.
        assert abs(rec["quality_rate"] - 0.92) < 1e-9

    def test_high_sample_count_is_high_confidence(self):
        _insert_rows(
            self.db_path,
            [{"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.012}
             for _ in range(15)],
        )
        recs = recommend(task_category="code", available_models=["sonnet"])
        assert recs[0]["confidence"] == "high"


# ---------------------------------------------------------------------------
# Ranking order (AC-4)
# ---------------------------------------------------------------------------

class TestRankingOrder:
    @pytest.fixture(autouse=True)
    def _fresh_db(self, tmp_path):
        self.db_path = _make_db(tmp_path)
        invalidate_stats_cache()
        yield
        os.environ.pop("TELEMETRY_DB_PATH", None)
        invalidate_stats_cache()

    def test_higher_quality_per_dollar_ranks_first(self):
        # cheap-good: quality 1.0 at cost 0.01 -> cost_per_pass 0.01, score 100
        # pricey-good: quality 1.0 at cost 0.10 -> cost_per_pass 0.10, score 10
        rows = []
        for _ in range(12):
            rows.append({"model_id": "gpt-5", "quality_verdict": "pass", "cost_usd": 0.01})
            rows.append({"model_id": "deepseek-v4", "quality_verdict": "pass", "cost_usd": 0.10})
        _insert_rows(self.db_path, rows)

        recs = recommend(
            task_category="code",
            available_models=["gpt-5", "deepseek-v4"],
        )
        assert [r["model_id"] for r in recs] == ["gpt-5", "deepseek-v4"]
        assert recs[0]["rank"] == 1
        assert recs[1]["rank"] == 2
        # cost_per_pass computed correctly.
        assert abs(recs[0]["cost_per_pass"] - 0.01) < 1e-9
        assert abs(recs[1]["cost_per_pass"] - 0.10) < 1e-9

    def test_tie_broken_by_cheaper_avg_cost(self):
        # Both have identical quality-per-dollar score, so avg_cost ascending wins.
        # cheap: quality 0.5 at cost 0.01 -> score = 0.5 / (0.01/0.5) = 25
        # expensive: quality 1.0 at cost 0.04 -> score = 1.0 / (0.04/1.0) = 25
        rows = []
        for i in range(12):
            verdict_cheap = "pass" if i % 2 == 0 else "fail"  # 0.5 quality
            rows.append({"model_id": "gpt-5", "quality_verdict": verdict_cheap, "cost_usd": 0.01})
            rows.append({"model_id": "deepseek-v4", "quality_verdict": "pass", "cost_usd": 0.04})
        _insert_rows(self.db_path, rows)

        recs = recommend(
            task_category="code",
            available_models=["gpt-5", "deepseek-v4"],
        )
        scores = [r["quality_rate"] / r["cost_per_pass"] for r in recs]
        assert abs(scores[0] - scores[1]) < 1e-6  # tie
        # Tie-break: cheaper avg_cost_usd first.
        assert recs[0]["model_id"] == "gpt-5"
        assert recs[0]["avg_cost_usd"] <= recs[1]["avg_cost_usd"]

    def test_rationale_string_present(self):
        _insert_rows(
            self.db_path,
            [{"model_id": "gpt-5", "quality_verdict": "pass", "cost_usd": 0.01}
             for _ in range(12)],
        )
        recs = recommend(task_category="code", available_models=["gpt-5"])
        assert isinstance(recs[0]["rationale"], str)
        assert recs[0]["rationale"]


# ---------------------------------------------------------------------------
# MCP tool — unknown vendor rejection (T-717)
# ---------------------------------------------------------------------------

class TestRecommendModelTool:
    @pytest.fixture(autouse=True)
    def _fresh_db(self, tmp_path):
        self.db_path = _make_db(tmp_path)
        invalidate_stats_cache()
        yield
        os.environ.pop("TELEMETRY_DB_PATH", None)
        invalidate_stats_cache()

    def test_rejects_unknown_vendor(self):
        result = recommend_model(
            {"task_category": "code", "exclude_vendors": ["acme-corp"]}
        )
        assert isinstance(result, dict)
        assert "error" in result
        assert "acme-corp" in result["error"]

    def test_accepts_known_vendor(self):
        result = recommend_model(
            {"task_category": "code", "exclude_vendors": ["anthropic"]}
        )
        assert isinstance(result, list)

    def test_requires_task_category(self):
        result = recommend_model({"exclude_vendors": []})
        assert isinstance(result, dict)
        assert "error" in result

    def test_registered_in_registry_and_authz(self):
        from depthfusion.mcp.authz import TOOL_CAPABILITIES
        from depthfusion.mcp.tools._registry import _TOOL_FLAGS, TOOLS

        assert "recommend_model" in TOOLS
        assert "recommend_model" in _TOOL_FLAGS
        assert "recommend_model" in TOOL_CAPABILITIES


# ---------------------------------------------------------------------------
# FastAPI endpoint POST /api/recommend-model (T-718, AC-7)
# ---------------------------------------------------------------------------

class TestRecommendModelEndpoint:
    @pytest.fixture(autouse=True)
    def _fresh_db(self, tmp_path, monkeypatch):
        self.db_path = _make_db(tmp_path)
        monkeypatch.setenv("TELEMETRY_DB_PATH", str(self.db_path))
        invalidate_stats_cache()
        yield
        invalidate_stats_cache()

    @pytest.fixture()
    def client(self):
        from depthfusion.api.auth import _require_principal_dep
        from depthfusion.api.rest import app
        from depthfusion.identity.models import Principal

        fake_principal = Principal(principal_id="test-user", groups=["viewer"])
        app.dependency_overrides[_require_principal_dep] = lambda: fake_principal
        c = TestClient(app, raise_server_exceptions=True)
        yield c
        app.dependency_overrides.clear()

    def test_endpoint_returns_ranked_list(self, client):
        _insert_rows(
            self.db_path,
            [{"model_id": "gpt-5", "quality_verdict": "pass", "cost_usd": 0.01}
             for _ in range(12)]
            + [{"model_id": "deepseek-v4", "quality_verdict": "pass", "cost_usd": 0.10}
               for _ in range(12)],
        )
        invalidate_stats_cache()
        resp = client.post(
            "/api/recommend-model",
            json={
                "task_category": "code",
                "available_models": ["gpt-5", "deepseek-v4"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["model_id"] == "gpt-5"
        assert data[0]["rank"] == 1

    def test_endpoint_vendor_exclusion(self, client):
        _insert_rows(
            self.db_path,
            [{"model_id": "sonnet", "quality_verdict": "pass", "cost_usd": 0.012}
             for _ in range(12)]
            + [{"model_id": "gpt-5", "quality_verdict": "pass", "cost_usd": 0.02}
               for _ in range(12)],
        )
        invalidate_stats_cache()
        resp = client.post(
            "/api/recommend-model",
            json={
                "task_category": "review",
                "exclude_vendors": ["anthropic"],
                "available_models": ["sonnet", "gpt-5"],
            },
        )
        assert resp.status_code == 200
        model_ids = {r["model_id"] for r in resp.json()}
        assert "sonnet" not in model_ids
        assert "gpt-5" in model_ids

    def test_endpoint_rejects_unknown_vendor(self, client):
        resp = client.post(
            "/api/recommend-model",
            json={"task_category": "code", "exclude_vendors": ["acme-corp"]},
        )
        assert resp.status_code == 400

    def test_endpoint_requires_auth(self):
        from depthfusion.api.rest import app

        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/api/recommend-model", json={"task_category": "code"})
        assert resp.status_code in (401, 403, 422)
