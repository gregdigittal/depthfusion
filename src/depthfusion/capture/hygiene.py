"""Nightly hygiene orchestrator — S-248.

Coordinates the three existing hygiene primitives in the canonical order:

    decay → dedup → prune

Running all three in sequence ensures that:
1. Salience scores are refreshed before dedup compares items.
2. Dedup supersedes stale near-duplicates before prune assesses age.
3. Prune then acts on a clean, current-scored corpus.

A ``hygiene_report`` :class:`~depthfusion.core.types.ContextItem` tagged
``['hygiene', project_slug]`` is published to the FileBus after each project
run so other agents and the dashboard can observe hygiene activity.

Env vars
--------
DEPTHFUSION_HYGIENE_SKIP
    Set to ``1`` to disable the scheduler entirely (for test environments).
    The scheduler is never started; ``run_hygiene_for_project`` can still be
    called directly in tests.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Module-level imports of hygiene primitives and bus — required so that
# patch("depthfusion.capture.hygiene.apply_decay", ...) works in tests.
# All three primitives are in the same package so there is no circular risk.
from depthfusion.capture.decay import apply_decay, DecaySummary  # noqa: F401
from depthfusion.capture.dedup import dedup_against_corpus  # noqa: F401
from depthfusion.capture.pruner import identify_candidates  # noqa: F401
from depthfusion.core.types import ContextItem  # noqa: F401
from depthfusion.router.bus import FileBus  # noqa: F401

# APScheduler is an optional dep — imported lazily inside build_scheduler()
# to avoid ImportError when it is not installed (test and local environments).
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
    _APSCHEDULER_AVAILABLE = True
except ImportError:  # pragma: no cover
    AsyncIOScheduler = None  # type: ignore[assignment,misc]
    CronTrigger = None  # type: ignore[assignment]
    _APSCHEDULER_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-project hygiene run  (S-248 AC-3)
# ---------------------------------------------------------------------------


def run_hygiene_for_project(
    project_slug: str,
    discoveries_dir: Optional[Path] = None,
) -> dict:
    """Execute decay → dedup → prune for *project_slug*.

    Returns a plain dict summarising the run (suitable for JSON serialisation
    and for embedding in the ``hygiene_report`` ContextItem content field).

    Never raises — all errors are caught and included in the summary dict
    so the scheduler does not crash the server on a transient failure.
    """
    result: dict = {
        "project_slug": project_slug,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay": {},
        "dedup": {},
        "prune": {},
        "errors": [],
    }

    # ── Step 1: decay ────────────────────────────────────────────────────────
    try:
        decay_summary: DecaySummary = apply_decay(discovery_dir=discoveries_dir)
        result["decay"] = {
            "total": decay_summary.total,
            "decayed": decay_summary.decayed,
            "archived": decay_summary.archived,
            "skipped": decay_summary.skipped,
        }
        logger.info(
            "hygiene[%s] decay done: total=%d decayed=%d archived=%d",
            project_slug,
            decay_summary.total,
            decay_summary.decayed,
            decay_summary.archived,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hygiene[%s] decay error: %s", project_slug, exc)
        result["errors"].append({"phase": "decay", "error": str(exc)})

    # ── Step 2: dedup ────────────────────────────────────────────────────────
    # dedup_against_corpus operates file-by-file; for the nightly job we run it
    # across all discovery files (each file is tested as the "new" candidate
    # against the rest of the corpus so near-duplicates accumulate superseded
    # markers). This is a lightweight pass — the underlying function never raises.
    dedup_superseded: list[str] = []
    try:
        disc_dir = discoveries_dir or (Path.home() / ".claude" / "shared" / "discoveries")
        if disc_dir.exists():
            files = sorted(
                f for f in disc_dir.iterdir()
                if f.is_file() and f.suffix == ".md" and not f.name.startswith(".")
            )
            for md_file in files:
                superseded = dedup_against_corpus(md_file, output_dir=disc_dir)
                dedup_superseded.extend(str(p) for p in superseded)
        result["dedup"] = {"superseded_count": len(dedup_superseded)}
        logger.info(
            "hygiene[%s] dedup done: superseded=%d",
            project_slug,
            len(dedup_superseded),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hygiene[%s] dedup error: %s", project_slug, exc)
        result["errors"].append({"phase": "dedup", "error": str(exc)})

    # ── Step 3: prune ────────────────────────────────────────────────────────
    try:
        candidates = identify_candidates(output_dir=discoveries_dir)
        result["prune"] = {"candidates_count": len(candidates)}
        logger.info(
            "hygiene[%s] prune done: candidates=%d",
            project_slug,
            len(candidates),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hygiene[%s] prune error: %s", project_slug, exc)
        result["errors"].append({"phase": "prune", "error": str(exc)})

    return result


def _publish_hygiene_report(project_slug: str, run_result: dict) -> None:
    """Publish a ``hygiene_report`` ContextItem tagged ['hygiene', project_slug].

    Failures here are logged and swallowed — the bus being unavailable must
    not prevent the hygiene run result from being returned.
    """
    try:
        content = json.dumps(run_result, indent=2)
        item = ContextItem(
            item_id=str(uuid.uuid4()),
            content=content,
            source_agent="hygiene-scheduler",
            tags=["hygiene", project_slug],
            priority="low",
            metadata={"report_type": "hygiene_report", "project_slug": project_slug},
        )
        bus = FileBus()
        bus.publish(item)
        logger.info("hygiene[%s] report published to bus", project_slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hygiene[%s] could not publish report: %s", project_slug, exc)


# ---------------------------------------------------------------------------
# Scheduler wiring  (S-248 AC-1, AC-2)
# ---------------------------------------------------------------------------

_DEFAULT_CRON_EXPRESSION = "0 2 * * *"  # 02:00 UTC daily


def _parse_cron_fields(expression: str) -> dict:
    """Parse a 5-field cron expression into kwargs for APScheduler CronTrigger.

    Raises ``ValueError`` for expressions that are not exactly 5 whitespace-
    separated fields or that APScheduler itself rejects.
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron expression must have exactly 5 fields, got {len(parts)}: {expression!r}"
        )
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
        "timezone": "UTC",
    }


async def _nightly_hygiene_job() -> None:
    """APScheduler async job: run hygiene for all registered projects."""
    try:
        from depthfusion.core.project_registry import ProjectRegistry
        registry = ProjectRegistry()
        projects = registry.list_projects()
    except Exception as exc:  # noqa: BLE001
        logger.warning("hygiene job: could not load project registry: %s", exc)
        projects = []

    if not projects:
        # Fall back to a single unnamed run when no projects are registered.
        slug = "default"
        result = run_hygiene_for_project(slug)
        _publish_hygiene_report(slug, result)
        return

    for project in projects:
        slug = project.slug
        local_path = Path(project.local_path) if project.local_path else None
        discoveries_dir: Optional[Path] = None
        if local_path and local_path.exists():
            # Convention: project-scoped discoveries live under
            # <local_path>/.claude/shared/discoveries/ — fall back to global.
            candidate = local_path / ".claude" / "shared" / "discoveries"
            if candidate.exists():
                discoveries_dir = candidate
        result = run_hygiene_for_project(slug, discoveries_dir=discoveries_dir)
        _publish_hygiene_report(slug, result)


def build_scheduler():
    """Construct and return an APScheduler AsyncIOScheduler with the hygiene job.

    Returns ``None`` when:
    - ``DEPTHFUSION_HYGIENE_SKIP=1`` is set (test bypass).
    - APScheduler is not installed (ImportError).
    - The cron expression is invalid (logs error, returns None).

    In all failure cases the function logs appropriately and returns ``None``
    so the caller (lifespan) can handle the absence gracefully.
    """
    if os.getenv("DEPTHFUSION_HYGIENE_SKIP", "").strip() == "1":
        logger.info("hygiene scheduler disabled via DEPTHFUSION_HYGIENE_SKIP=1")
        return None

    if not _APSCHEDULER_AVAILABLE or AsyncIOScheduler is None or CronTrigger is None:
        logger.warning(
            "hygiene scheduler: apscheduler not installed — scheduler disabled"
        )
        return None

    cron_expr = os.getenv("DEPTHFUSION_HYGIENE_SCHEDULE", _DEFAULT_CRON_EXPRESSION).strip()
    try:
        cron_fields = _parse_cron_fields(cron_expr)
        trigger = CronTrigger(**cron_fields)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "hygiene scheduler: invalid cron expression %r — %s; scheduler disabled",
            cron_expr,
            exc,
        )
        return None

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _nightly_hygiene_job,
        trigger=trigger,
        id="nightly_hygiene",
        name="Nightly hygiene (decay→dedup→prune)",
        misfire_grace_time=3600,
        coalesce=True,
    )
    logger.info(
        "hygiene scheduler configured: cron=%r next_run will fire at 02:00 UTC",
        cron_expr,
    )
    return scheduler
