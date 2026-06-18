"""Tests for learned model performance statistics (S-209)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from depthfusion.analytics.model_stats import (
    get_model_stats,
    invalidate_model_stats_cache,
)


@pytest.fixture()
def telemetry_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from depthfusion.telemetry import recorder, schema

    db_path = tmp_path / "model_telemetry.db"
    monkeypatch.setenv("TELEMETRY_DB_PATH", str(db_path))
    recorder._migrated = False
    recorder._migrated_path = None
    schema.migrate()
    invalidate_model_stats_cache()
    yield db_path
    invalidate_model_stats_cache()
    recorder._migrated = False
    recorder._migrated_path = None


def _insert_rows(db_path: Path, rows: list[dict]) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        for index, row in enumerate(rows):
            conn.execute(
                """
                INSERT INTO model_telemetry
                    (recorded_at, session_id, model_id, task_category,
                     tokens_in, tokens_out, latency_ms, cost_usd,
                     quality_verdict, project_slug)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("recorded_at", "2026-06-01T00:00:00+00:00"),
                    row.get("session_id", f"sess-{index}"),
                    row.get("model_id", "custom-model"),
                    row.get("task_category", "code"),
                    row.get("tokens_in", 100),
                    row.get("tokens_out", 50),
                    row.get("latency_ms", 500),
                    row.get("cost_usd", 0.01),
                    row.get("quality_verdict", "pass"),
                    row.get("project_slug", "depthfusion"),
                ),
            )
        conn.commit()


def _stat(stats: list[dict], model_id: str, task_category: str = "code") -> dict:
    return next(
        row
        for row in stats
        if row["model_id"] == model_id and row["task_category"] == task_category
    )


def test_stat_computation(telemetry_db: Path) -> None:
    _insert_rows(
        telemetry_db,
        [
            {"quality_verdict": "pass", "cost_usd": 0.01, "latency_ms": 100, "tokens_out": 10},
            {"quality_verdict": "pass", "cost_usd": 0.02, "latency_ms": 200, "tokens_out": 20},
            {"quality_verdict": "fail", "cost_usd": 0.03, "latency_ms": 300, "tokens_out": 30},
            {"quality_verdict": "fail", "cost_usd": 0.04, "latency_ms": 400, "tokens_out": 40},
            {"quality_verdict": "pass", "cost_usd": 0.05, "latency_ms": 500, "tokens_out": 50},
            {"quality_verdict": "pass", "cost_usd": 0.06, "latency_ms": 600, "tokens_out": 60},
            {"quality_verdict": "fail", "cost_usd": 0.07, "latency_ms": 700, "tokens_out": 70},
            {"quality_verdict": "pass", "cost_usd": 0.08, "latency_ms": 800, "tokens_out": 80},
            {"quality_verdict": "pass", "cost_usd": 0.09, "latency_ms": 900, "tokens_out": 90},
            {"quality_verdict": "fail", "cost_usd": 0.10, "latency_ms": 1000, "tokens_out": 100},
        ],
    )

    row = _stat(get_model_stats(model_id="custom-model", task_category="code"), "custom-model")

    assert row["sample_count"] == 10
    assert row["source"] == "observed"
    assert row["quality_rate"] == pytest.approx(0.6)
    assert row["avg_cost_usd"] == pytest.approx(0.055)
    assert row["cost_per_pass"] == pytest.approx(0.055 / 0.6)
    assert row["avg_tokens_out"] == pytest.approx(55)
    assert row["avg_duration_ms"] == pytest.approx(550)
    assert row["last_seen"] == "2026-06-01T00:00:00+00:00"


def test_cache_invalidation(telemetry_db: Path) -> None:
    from depthfusion.telemetry.recorder import record_event

    _insert_rows(telemetry_db, [{"model_id": "custom-model", "quality_verdict": "pass"}])
    first = _stat(get_model_stats(model_id="custom-model"), "custom-model")
    second = _stat(get_model_stats(model_id="custom-model"), "custom-model")
    assert second["sample_count"] == first["sample_count"] == 1

    record_event(
        {
            "session_id": "new-session",
            "model_id": "custom-model",
            "task_category": "code",
            "tokens_in": 101,
            "tokens_out": 51,
            "latency_ms": 501,
            "cost_usd": 0.02,
            "quality_verdict": "fail",
            "recorded_at": "2026-06-02T00:00:00+00:00",
        }
    )

    refreshed = _stat(get_model_stats(model_id="custom-model"), "custom-model")
    assert refreshed["sample_count"] == 2


def test_prior_blending_low_n(telemetry_db: Path) -> None:
    _insert_rows(
        telemetry_db,
        [
            {"model_id": "sonnet", "quality_verdict": "fail", "cost_usd": 0.03},
            {"model_id": "sonnet", "quality_verdict": "fail", "cost_usd": 0.03},
            {"model_id": "sonnet", "quality_verdict": "fail", "cost_usd": 0.03},
        ],
    )

    row = _stat(get_model_stats(model_id="sonnet", task_category="code"), "sonnet")

    assert row["source"] == "blended"
    assert row["sample_count"] == 3
    assert 0 < row["quality_rate"] < 0.86


def test_prior_only(telemetry_db: Path) -> None:
    row = _stat(get_model_stats(model_id="gpt-4o-mini", task_category="review"), "gpt-4o-mini", "review")

    assert row["source"] == "prior"
    assert row["sample_count"] == 0
    assert row["quality_rate"] == pytest.approx(0.70)


def test_all_time_window(telemetry_db: Path) -> None:
    _insert_rows(
        telemetry_db,
        [
            {
                "model_id": "custom-model",
                "quality_verdict": "pass",
                "recorded_at": "2020-01-01T00:00:00+00:00",
            }
        ],
    )

    recent = get_model_stats(model_id="custom-model", task_category="code", window_days=30)
    all_time = get_model_stats(model_id="custom-model", task_category="code", window_days=0)

    assert recent == []
    assert _stat(all_time, "custom-model")["sample_count"] == 1
