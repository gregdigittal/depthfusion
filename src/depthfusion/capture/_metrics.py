"""Shared metrics-emission helper for capture mechanisms — S-60 / T-187..T-190.

Every capture call-site (decision_extractor, negative_extractor, dedup,
git_post_commit, confirm_discovery) emits a single `capture` JSONL event
via `MetricsCollector.record_capture_event()` at write time. All
emissions go through `emit_capture_event()` so:

  * Metrics failures are swallowed uniformly — no capture mechanism can
    have observability errors propagate into the hot path (a failing
    metrics write must never block a git commit or break recall).
  * Import is deferred inside the function so modules that can't find
    the metrics collector (test stubs, minimal deploys) still work.
  * The `event_subtype` default of "ok" is consistent with the
    collector's validation contract.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def emit_capture_event(
    *,
    capture_mechanism: str,
    project: str = "unknown",
    session_id: str = "",
    write_success: bool = True,
    entries_written: int = 0,
    file_path: str = "",
    event_subtype: str = "ok",
    config_version_id: str = "",
) -> None:
    """Emit a structured capture-event JSONL record. Never raises.

    This is the single call-site interface for capture mechanisms;
    individual modules (decision_extractor, dedup, etc.) import it and
    call once per write attempt. The fail-closed try/except here is
    defense in depth — `MetricsCollector.record_capture_event` also
    swallows internally, but we guard the import itself.
    """
    try:
        from depthfusion.metrics.collector import MetricsCollector
        MetricsCollector().record_capture_event(
            capture_mechanism=capture_mechanism,
            project=project,
            session_id=session_id,
            write_success=write_success,
            entries_written=entries_written,
            file_path=file_path,
            event_subtype=event_subtype,
            config_version_id=config_version_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "capture metrics emission failed (mechanism=%s): %s",
            capture_mechanism, exc,
        )


__all__ = ["emit_capture_event"]
