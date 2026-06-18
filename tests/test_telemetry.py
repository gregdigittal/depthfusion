from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest


@pytest.fixture()
def telemetry_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from depthfusion.telemetry import recorder

    db_path = tmp_path / "model_telemetry.db"
    monkeypatch.setenv("TELEMETRY_DB_PATH", str(db_path))
    recorder._migrated = False
    recorder._migrated_path = None
    return db_path


def _event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "session_id": "session-1",
        "model_id": "claude-sonnet",
        "task_category": "code",
        "tokens_in": 100,
        "tokens_out": 25,
        "latency_ms": 750,
        "cost_usd": 0.0012,
    }
    event.update(overrides)
    return event


def test_record_event_happy_path(telemetry_db: Path) -> None:
    from depthfusion.telemetry.recorder import record_event

    result = record_event(_event())

    assert result == {"id": 1, "deduplicated": False}
    assert telemetry_db.exists()


def test_record_event_dedup_within_60s(telemetry_db: Path) -> None:
    from depthfusion.telemetry.recorder import record_event

    first = record_event(_event(recorded_at="2026-06-18T12:00:00+00:00"))
    second = record_event(_event(recorded_at="2026-06-18T12:00:30+00:00"))

    assert first == {"id": 1, "deduplicated": False}
    assert second == {"id": 1, "deduplicated": True}


def test_invalid_task_category_rejected(telemetry_db: Path) -> None:
    from depthfusion.mcp.tools.telemetry_tools import _tool_record_model_telemetry
    from depthfusion.telemetry.recorder import record_event

    with pytest.raises(ValueError, match="Invalid task_category"):
        record_event(_event(task_category="invalid"))

    response = json.loads(_tool_record_model_telemetry(_event(task_category="invalid")))
    assert response["error"] == "Invalid task_category: 'invalid'"


def test_schema_round_trip_preserves_fields(telemetry_db: Path) -> None:
    from depthfusion.telemetry import schema
    from depthfusion.telemetry.recorder import record_event

    event = _event(
        session_id="session-2",
        model_id="gpt-4.1",
        task_category="search",
        tokens_in=123,
        tokens_out=456,
        latency_ms=789,
        cost_usd=0.0123,
        quality_verdict="pass",
        project_slug="depthfusion",
        recorded_at="2026-06-18T12:34:56+00:00",
    )
    result = record_event(event)

    with closing(schema.connect()) as conn:
        row = conn.execute(
            "SELECT * FROM model_telemetry WHERE id = ?", (result["id"],)
        ).fetchone()

    assert dict(row) == {
        "id": 1,
        "recorded_at": "2026-06-18T12:34:56+00:00",
        "session_id": "session-2",
        "model_id": "gpt-4.1",
        "task_category": "search",
        "tokens_in": 123,
        "tokens_out": 456,
        "latency_ms": 789,
        "cost_usd": 0.0123,
        "quality_verdict": "pass",
        "project_slug": "depthfusion",
    }


@pytest.mark.asyncio
async def test_recent_endpoint_returns_records(telemetry_db: Path) -> None:
    import depthfusion.api.rest as rest_module
    from depthfusion.identity.models import Principal
    from depthfusion.telemetry.recorder import record_event

    record_event(
        _event(
            session_id="session-api",
            model_id="claude-sonnet",
            task_category="summarise",
            tokens_in=20,
            tokens_out=5,
            latency_ms=250,
            cost_usd=0.0007,
            quality_verdict="pass",
            project_slug="depthfusion",
            recorded_at="2026-06-18T13:00:00+00:00",
        )
    )

    principal = Principal(principal_id="test-user", upn="test@example.local")
    body = await rest_module.get_recent_model_telemetry(
        limit=100,
        project_slug="depthfusion",
        model_id=None,
        principal=principal,
    )

    assert body[0]["session_id"] == "session-api"
    assert body[0]["model_id"] == "claude-sonnet"
    assert body[0]["task_category"] == "summarise"
