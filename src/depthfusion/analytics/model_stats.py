"""Learned model performance statistics for model telemetry (S-209)."""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from depthfusion.telemetry.schema import connect, migrate

CACHE_TTL_SECONDS = 60 * 60
DEFAULT_WINDOW_DAYS = 30
PRIOR_WEIGHT = 10


_PRIOR_ROWS: tuple[dict[str, Any], ...] = (
    {"model_id": "opus", "task_category": "code", "quality_rate": 0.94, "avg_cost_usd": 0.060, "avg_tokens_out": 1400, "avg_duration_ms": 5200},
    {"model_id": "opus", "task_category": "review", "quality_rate": 0.95, "avg_cost_usd": 0.050, "avg_tokens_out": 1100, "avg_duration_ms": 4800},
    {"model_id": "opus", "task_category": "planning", "quality_rate": 0.92, "avg_cost_usd": 0.045, "avg_tokens_out": 900, "avg_duration_ms": 4300},
    {"model_id": "sonnet", "task_category": "code", "quality_rate": 0.86, "avg_cost_usd": 0.018, "avg_tokens_out": 1200, "avg_duration_ms": 3300},
    {"model_id": "sonnet", "task_category": "review", "quality_rate": 0.84, "avg_cost_usd": 0.014, "avg_tokens_out": 950, "avg_duration_ms": 3000},
    {"model_id": "sonnet", "task_category": "planning", "quality_rate": 0.82, "avg_cost_usd": 0.012, "avg_tokens_out": 800, "avg_duration_ms": 2800},
    {"model_id": "haiku", "task_category": "code", "quality_rate": 0.68, "avg_cost_usd": 0.004, "avg_tokens_out": 900, "avg_duration_ms": 1600},
    {"model_id": "haiku", "task_category": "review", "quality_rate": 0.66, "avg_cost_usd": 0.003, "avg_tokens_out": 750, "avg_duration_ms": 1450},
    {"model_id": "haiku", "task_category": "planning", "quality_rate": 0.64, "avg_cost_usd": 0.003, "avg_tokens_out": 650, "avg_duration_ms": 1350},
    {"model_id": "gpt-4o", "task_category": "code", "quality_rate": 0.88, "avg_cost_usd": 0.020, "avg_tokens_out": 1200, "avg_duration_ms": 3200},
    {"model_id": "gpt-4o", "task_category": "review", "quality_rate": 0.86, "avg_cost_usd": 0.016, "avg_tokens_out": 950, "avg_duration_ms": 3000},
    {"model_id": "gpt-4o", "task_category": "planning", "quality_rate": 0.84, "avg_cost_usd": 0.014, "avg_tokens_out": 800, "avg_duration_ms": 2700},
    {"model_id": "gpt-4o-mini", "task_category": "code", "quality_rate": 0.72, "avg_cost_usd": 0.003, "avg_tokens_out": 900, "avg_duration_ms": 1500},
    {"model_id": "gpt-4o-mini", "task_category": "review", "quality_rate": 0.70, "avg_cost_usd": 0.0025, "avg_tokens_out": 750, "avg_duration_ms": 1400},
    {"model_id": "gpt-4o-mini", "task_category": "planning", "quality_rate": 0.69, "avg_cost_usd": 0.002, "avg_tokens_out": 650, "avg_duration_ms": 1300},
    {"model_id": "codex-5.5", "task_category": "code", "quality_rate": 0.90, "avg_cost_usd": 0.025, "avg_tokens_out": 1300, "avg_duration_ms": 3600},
    {"model_id": "codex-5.5", "task_category": "review", "quality_rate": 0.87, "avg_cost_usd": 0.020, "avg_tokens_out": 1000, "avg_duration_ms": 3300},
    {"model_id": "codex-5.5", "task_category": "planning", "quality_rate": 0.80, "avg_cost_usd": 0.016, "avg_tokens_out": 800, "avg_duration_ms": 3000},
)

_ALIASES: tuple[tuple[str, str], ...] = (
    ("claude-opus", "opus"),
    ("opus", "opus"),
    ("claude-sonnet", "sonnet"),
    ("sonnet", "sonnet"),
    ("claude-haiku", "haiku"),
    ("haiku", "haiku"),
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o", "gpt-4o"),
    ("codex-5.5", "codex-5.5"),
)


class _StatCache:
    """Small in-process LRU cache with TTL."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, maxsize: int = 128) -> None:
        self._ttl = ttl
        self._maxsize = maxsize
        self._entries: OrderedDict[tuple[Optional[str], Optional[str], int], tuple[float, list[dict[str, Any]]]] = OrderedDict()

    def get(
        self,
        key: tuple[Optional[str], Optional[str], int],
        clock: Callable[[], float] = time.time,
    ) -> Optional[list[dict[str, Any]]]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        created_at, value = entry
        if clock() - created_at >= self._ttl:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return [dict(row) for row in value]

    def set(
        self,
        key: tuple[Optional[str], Optional[str], int],
        value: list[dict[str, Any]],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._entries[key] = (clock(), [dict(row) for row in value])
        self._entries.move_to_end(key)
        while len(self._entries) > self._maxsize:
            self._entries.popitem(last=False)

    def invalidate(self) -> None:
        self._entries.clear()


_cache = _StatCache()


def _canonical_model_id(model_id: str) -> str:
    lower = model_id.lower()
    for pattern, canonical in _ALIASES:
        if pattern in lower:
            return canonical
    return lower


def _prior_for(model_id: str, task_category: str) -> Optional[dict[str, Any]]:
    canonical = _canonical_model_id(model_id)
    for row in _PRIOR_ROWS:
        if row["model_id"] == canonical and row["task_category"] == task_category:
            return dict(row)
    return None


def _get_prior(model_id: str, task_category: str = "code") -> Optional[dict[str, Any]]:
    """Return a prior compatible with older recommendation code."""
    prior = _prior_for(model_id, task_category)
    if prior is not None:
        return prior
    canonical = _canonical_model_id(model_id)
    for row in _PRIOR_ROWS:
        if row["model_id"] == canonical:
            return dict(row)
    return None


def _confidence(sample_count: int) -> str:
    if sample_count < 5:
        return "low"
    if sample_count < 20:
        return "medium"
    return "high"


def _cost_per_pass(avg_cost_usd: float, quality_rate: float) -> float:
    if quality_rate <= 0:
        return float("inf")
    return avg_cost_usd / quality_rate


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _observed_stats(rows: list[dict[str, Any]], window_days: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["model_id"]), str(row["task_category"]))
        groups.setdefault(key, []).append(row)

    stats: list[dict[str, Any]] = []
    for (model_id, task_category), group in groups.items():
        sample_count = len(group)
        pass_count = sum(
            1 for row in group if str(row.get("quality_verdict") or "").lower() == "pass"
        )
        quality_rate = pass_count / sample_count if sample_count else 0.0
        avg_cost_usd = sum(float(row["cost_usd"]) for row in group) / sample_count
        avg_tokens_out = sum(float(row["tokens_out"]) for row in group) / sample_count
        durations = [float(row["latency_ms"]) for row in group]
        avg_duration_ms = sum(durations) / sample_count
        last_seen = max(str(row["recorded_at"]) for row in group)
        stat = {
            "model_id": model_id,
            "task_category": task_category,
            "window_days": window_days,
            "sample_count": sample_count,
            "quality_rate": quality_rate,
            "avg_cost_usd": avg_cost_usd,
            "avg_tokens_out": avg_tokens_out,
            "avg_duration_ms": avg_duration_ms,
            "cost_per_pass": _cost_per_pass(avg_cost_usd, quality_rate),
            "p50_duration_ms": _percentile(durations, 0.50),
            "p95_duration_ms": _percentile(durations, 0.95),
            "last_seen": last_seen,
            "confidence": _confidence(sample_count),
            "source": "observed",
        }
        stats.append(stat)
    return stats


def _prior_stat(model_id: str, task_category: str, window_days: int) -> dict[str, Any]:
    prior = _prior_for(model_id, task_category)
    if prior is None:
        raise KeyError((model_id, task_category))
    quality_rate = float(prior["quality_rate"])
    avg_cost_usd = float(prior["avg_cost_usd"])
    avg_duration_ms = float(prior["avg_duration_ms"])
    return {
        "model_id": model_id,
        "task_category": task_category,
        "window_days": window_days,
        "sample_count": 0,
        "quality_rate": quality_rate,
        "avg_cost_usd": avg_cost_usd,
        "avg_tokens_out": float(prior["avg_tokens_out"]),
        "avg_duration_ms": avg_duration_ms,
        "cost_per_pass": _cost_per_pass(avg_cost_usd, quality_rate),
        "p50_duration_ms": avg_duration_ms,
        "p95_duration_ms": avg_duration_ms,
        "last_seen": None,
        "confidence": "low",
        "source": "prior",
    }


def _blend_stat(observed: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    sample_count = int(observed["sample_count"])
    total_weight = sample_count + PRIOR_WEIGHT

    def blend(field: str) -> float:
        return (
            float(observed[field]) * sample_count + float(prior[field]) * PRIOR_WEIGHT
        ) / total_weight

    quality_rate = blend("quality_rate")
    avg_cost_usd = blend("avg_cost_usd")
    avg_duration_ms = blend("avg_duration_ms")
    blended = dict(observed)
    blended.update(
        {
            "quality_rate": quality_rate,
            "avg_cost_usd": avg_cost_usd,
            "avg_tokens_out": blend("avg_tokens_out"),
            "avg_duration_ms": avg_duration_ms,
            "cost_per_pass": _cost_per_pass(avg_cost_usd, quality_rate),
            "p50_duration_ms": blend("p50_duration_ms"),
            "p95_duration_ms": blend("p95_duration_ms"),
            "source": "blended",
        }
    )
    return blended


def _parse_recorded_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_in_window(row: dict[str, Any], cutoff: Optional[datetime]) -> bool:
    if cutoff is None:
        return True
    try:
        return _parse_recorded_at(str(row["recorded_at"])) >= cutoff
    except ValueError:
        return False


def _query_rows(
    model_id: Optional[str],
    task_category: Optional[str],
    window_days: int,
) -> list[dict[str, Any]]:
    migrate()
    where: list[str] = []
    params: list[object] = []
    if model_id is not None:
        where.append("model_id = ?")
        params.append(model_id)
    if task_category is not None:
        where.append("task_category = ?")
        params.append(task_category)

    query = "SELECT * FROM model_telemetry"
    if where:
        query += " WHERE " + " AND ".join(where)

    cutoff = None
    if window_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    with closing(connect()) as conn:
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    return [row for row in rows if _row_in_window(row, cutoff)]


def _prior_candidates(
    model_id: Optional[str],
    task_category: Optional[str],
    window_days: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for prior in _PRIOR_ROWS:
        if task_category is not None and prior["task_category"] != task_category:
            continue
        output_model_id = str(prior["model_id"])
        if model_id is not None:
            if _canonical_model_id(model_id) != output_model_id:
                continue
            output_model_id = model_id
        candidates.append(_prior_stat(output_model_id, str(prior["task_category"]), window_days))
    return candidates


def get_model_stats(
    model_id: Optional[str] = None,
    task_category: Optional[str] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    *,
    _clock: Callable[[], float] = time.time,
) -> list[dict[str, Any]]:
    """Return per-model, per-category performance statistics.

    ``window_days=0`` means all-time. Results with fewer than ten observed
    samples are blended with hard-coded priors when a matching prior exists.
    """
    if window_days < 0:
        raise ValueError("window_days must be >= 0")

    cache_key = (model_id, task_category, int(window_days))
    cached = _cache.get(cache_key, clock=_clock)
    if cached is not None:
        return cached

    observed = _observed_stats(_query_rows(model_id, task_category, int(window_days)), int(window_days))
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for stat in observed:
        key = (str(stat["model_id"]), str(stat["task_category"]))
        seen.add(key)
        if int(stat["sample_count"]) < 10:
            prior = _prior_stat(stat["model_id"], stat["task_category"], int(window_days)) if _prior_for(stat["model_id"], stat["task_category"]) else None
            results.append(_blend_stat(stat, prior) if prior is not None else stat)
        else:
            results.append(stat)

    for prior in _prior_candidates(model_id, task_category, int(window_days)):
        key = (str(prior["model_id"]), str(prior["task_category"]))
        if key not in seen:
            results.append(prior)

    results.sort(key=lambda row: (str(row["task_category"]), str(row["model_id"])))
    _cache.set(cache_key, results, clock=_clock)
    return [dict(row) for row in results]


def invalidate_model_stats_cache() -> None:
    """Invalidate all cached model statistics after telemetry writes."""
    _cache.invalidate()


def invalidate_stats_cache(model_id: Optional[str] = None) -> None:
    """Backward-compatible alias for older tests and callers."""
    invalidate_model_stats_cache()
