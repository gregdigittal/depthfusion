"""Recorder for model telemetry events."""
from __future__ import annotations

import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from depthfusion.telemetry import schema

TASK_CATEGORIES = schema.TASK_CATEGORIES
QUALITY_VERDICTS = schema.QUALITY_VERDICTS
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

_migrated = False
_migrated_path: Path | None = None
_lock = threading.Lock()


def ensure_migrated() -> None:
    """Run the SQLite migration once per process."""
    global _migrated, _migrated_path
    db_path = schema.get_db_path()
    if _migrated and _migrated_path == db_path:
        return
    with _lock:
        if not _migrated or _migrated_path != db_path:
            schema.migrate()
            _migrated = True
            _migrated_path = db_path


def _parse_recorded_at(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _require_event(event: dict[str, Any]) -> None:
    missing = sorted(field for field in REQUIRED_FIELDS if field not in event)
    if missing:
        raise ValueError(f"Missing required telemetry fields: {', '.join(missing)}")
    if event["task_category"] not in TASK_CATEGORIES:
        raise ValueError(f"Invalid task_category: {event['task_category']!r}")
    verdict = event.get("quality_verdict")
    if verdict is not None and verdict not in QUALITY_VERDICTS:
        raise ValueError(f"Invalid quality_verdict: {verdict!r}")


def record_event(event: dict) -> dict:
    """Record one model telemetry event.

    Returns ``{"id": int, "deduplicated": bool}``. If a matching event was
    recorded within 60 seconds, the existing row id is returned.
    """
    _require_event(event)
    ensure_migrated()

    session_id = str(event["session_id"])
    model_id = str(event["model_id"])
    task_category = str(event["task_category"])
    tokens_in = int(event["tokens_in"])
    tokens_out = int(event["tokens_out"])
    latency_ms = int(event["latency_ms"])
    cost_usd = float(event["cost_usd"])
    recorded_at = str(event.get("recorded_at") or _utc_iso_now())
    recorded_dt = _parse_recorded_at(recorded_at)
    dedup_window = timedelta(seconds=60)

    with _lock, closing(schema.connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, recorded_at
              FROM model_telemetry
             WHERE session_id = ?
               AND model_id = ?
               AND task_category = ?
               AND tokens_in = ?
               AND tokens_out = ?
             ORDER BY recorded_at DESC
            """,
            (session_id, model_id, task_category, tokens_in, tokens_out),
        ).fetchall()
        for row in rows:
            try:
                existing_dt = _parse_recorded_at(row["recorded_at"])
            except ValueError:
                continue
            if abs(recorded_dt - existing_dt) <= dedup_window:
                return {"id": int(row["id"]), "deduplicated": True}

        cursor = conn.execute(
            """
            INSERT INTO model_telemetry (
                recorded_at,
                session_id,
                model_id,
                task_category,
                tokens_in,
                tokens_out,
                latency_ms,
                cost_usd,
                quality_verdict,
                project_slug
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_at,
                session_id,
                model_id,
                task_category,
                tokens_in,
                tokens_out,
                latency_ms,
                cost_usd,
                event.get("quality_verdict"),
                event.get("project_slug"),
            ),
        )
        conn.commit()
        from depthfusion.analytics.model_stats import invalidate_model_stats_cache

        invalidate_model_stats_cache()
        return {"id": int(cursor.lastrowid), "deduplicated": False}
