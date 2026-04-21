"""MetricsAggregator — summarizes daily metrics from JSONL files.

v0.5.0 T-164 / S-53: extended with `backend_summary()` and
`capture_summary()` that read the structured `recall` and `capture`
streams and produce per-backend latency + error-rate tables.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

from depthfusion.metrics.collector import MetricsCollector


class MetricsAggregator:
    """Reads and aggregates daily metrics from the collector's JSONL files."""

    def __init__(self, collector: MetricsCollector) -> None:
        self.collector = collector

    # ------------------------------------------------------------------
    # v0.3 simple-metric summary (unchanged)
    # ------------------------------------------------------------------

    def daily_summary(self, target_date: date | None = None) -> dict:
        """Summarize metrics for target_date (default: today).

        Returns {metric_name: {count, sum, avg, min, max}}.
        """
        if target_date is None:
            target_date = date.today()

        file_path = self.collector.metrics_dir / f"{target_date.isoformat()}.jsonl"
        if not file_path.exists():
            return {}

        # Collect all values per metric
        buckets: dict[str, list[float]] = {}
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    name = entry["metric"]
                    value = float(entry["value"])
                    buckets.setdefault(name, []).append(value)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        summary: dict[str, dict] = {}
        for metric_name, values in buckets.items():
            count = len(values)
            total = sum(values)
            summary[metric_name] = {
                "count": count,
                "sum": total,
                "avg": total / count if count > 0 else 0.0,
                "min": min(values),
                "max": max(values),
            }
        return summary

    def format_for_digest(self, summary: dict) -> str:
        """Format summary as markdown for inclusion in daily digest."""
        if not summary:
            return "## DepthFusion Metrics\n\nNo metrics recorded for this period.\n"

        lines = ["## DepthFusion Metrics\n"]
        for metric_name, stats in sorted(summary.items()):
            lines.append(f"### `{metric_name}`")
            lines.append(f"- Count: {stats['count']}")
            lines.append(f"- Sum: {stats['sum']:.4f}")
            lines.append(f"- Avg: {stats['avg']:.4f}")
            lines.append(f"- Min: {stats['min']:.4f}")
            lines.append(f"- Max: {stats['max']:.4f}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # v0.5 T-164 / S-53 — structured stream summaries
    # ------------------------------------------------------------------

    def backend_summary(self, target_date: date | None = None) -> dict:
        """Summarise the recall-query stream for `target_date`.

        Returns:
          {
            "per_backend": {
              "<capability>::<backend_name>": {
                 "count": int,
                 "avg_latency_ms": float,
                 "p50_latency_ms": float,
                 "p95_latency_ms": float,
                 "error_count": int,
                 "error_rate": float,  # errors / total
              },
              ...
            },
            "per_capability_fallback": {
              "<capability>": [<chain_name1>, <chain_name2>, ...],
              ...
            },
            "total_queries": int,
            "total_errors": int,
            "overall_error_rate": float,
          }

        Returns an empty dict when no recall events were recorded for the
        target date. Malformed lines are skipped.
        """
        if target_date is None:
            target_date = date.today()

        file_path = (
            self.collector.metrics_dir / f"{target_date.isoformat()}-recall.jsonl"
        )
        entries = list(_iter_jsonl(file_path))
        if not entries:
            return {}

        per_backend_latencies: dict[str, list[float]] = {}
        per_backend_errors: dict[str, int] = {}
        per_backend_counts: dict[str, int] = {}   # MED-4: count queries even
                                                   # when latency isn't recorded
        fallback_chains: dict[str, set[str]] = {}
        total_errors = 0

        for entry in entries:
            subtype = entry.get("event_subtype", "ok")
            is_error = subtype != "ok"
            if is_error:
                total_errors += 1

            backends = entry.get("backend_used", {}) or {}
            latencies = entry.get("latency_ms_per_capability", {}) or {}
            chains = entry.get("backend_fallback_chain", {}) or {}

            for cap, name in backends.items():
                key = f"{cap}::{name}"
                # MED-4 fix: every backend that appears in ANY query gets a
                # per_backend bucket, even when no latency was recorded
                # (e.g. timeout paths). Without this, errors were counted
                # toward total_errors but vanished from per_backend.
                per_backend_counts[key] = per_backend_counts.get(key, 0) + 1
                per_backend_latencies.setdefault(key, [])
                lat = latencies.get(cap)
                if isinstance(lat, (int, float)):
                    per_backend_latencies[key].append(float(lat))
                if is_error:
                    per_backend_errors[key] = per_backend_errors.get(key, 0) + 1

            for cap, chain in chains.items():
                if isinstance(chain, list):
                    fallback_chains.setdefault(cap, set()).update(chain)

        per_backend: dict[str, dict] = {}
        for key, lats in per_backend_latencies.items():
            query_count = per_backend_counts.get(key, 0)
            err_count = per_backend_errors.get(key, 0)
            # `count` = number of queries that touched this backend (for
            # error-rate accuracy). `measured_count` = number of those
            # with a latency sample (latency stats exclude un-measured queries).
            measured_count = len(lats)
            per_backend[key] = {
                "count": query_count,
                "measured_count": measured_count,
                "avg_latency_ms": sum(lats) / measured_count if measured_count else 0.0,
                "p50_latency_ms": _percentile(lats, 0.50),
                "p95_latency_ms": _percentile(lats, 0.95),
                "error_count": err_count,
                "error_rate": err_count / query_count if query_count else 0.0,
            }

        total_queries = len(entries)
        return {
            "per_backend": per_backend,
            "per_capability_fallback": {
                cap: sorted(names) for cap, names in fallback_chains.items()
            },
            "total_queries": total_queries,
            "total_errors": total_errors,
            "overall_error_rate": (
                total_errors / total_queries if total_queries else 0.0
            ),
        }

    def capture_summary(self, target_date: date | None = None) -> dict:
        """Summarise the capture-event stream for `target_date`.

        Returns:
          {
            "per_mechanism": {
              "<mechanism>": {
                "total": int,
                "successes": int,
                "failures": int,
                "write_rate": float,          # successes / total
                "entries_written": int,       # sum across events
              },
              ...
            },
            "unknown_mechanisms": ["<name>", ...],  # mechanism names that
                                                     # aren't in the known enum
            "total_events": int,
            "total_entries_written": int,
          }

        Returns an empty dict when no capture events were recorded.
        """
        if target_date is None:
            target_date = date.today()

        file_path = (
            self.collector.metrics_dir / f"{target_date.isoformat()}-capture.jsonl"
        )
        entries = list(_iter_jsonl(file_path))
        if not entries:
            return {}

        per_mech: dict[str, dict[str, int]] = {}
        unknown_mechs: set[str] = set()
        total_entries_written = 0

        for entry in entries:
            mech = entry.get("capture_mechanism", "unknown")
            success = bool(entry.get("write_success", True))
            entries_written = int(entry.get("entries_written", 0) or 0)
            if not entry.get("capture_mechanism_known", True):
                unknown_mechs.add(mech)

            bucket = per_mech.setdefault(
                mech, {"total": 0, "successes": 0, "failures": 0, "entries_written": 0},
            )
            bucket["total"] += 1
            if success:
                bucket["successes"] += 1
            else:
                bucket["failures"] += 1
            bucket["entries_written"] += entries_written
            total_entries_written += entries_written

        final: dict[str, dict] = {}
        for mech, b in per_mech.items():
            total = b["total"]
            final[mech] = {
                "total": total,
                "successes": b["successes"],
                "failures": b["failures"],
                "write_rate": b["successes"] / total if total else 0.0,
                "entries_written": b["entries_written"],
            }

        return {
            "per_mechanism": final,
            "unknown_mechanisms": sorted(unknown_mechs),
            "total_events": sum(b["total"] for b in per_mech.values()),
            "total_entries_written": total_entries_written,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iter_jsonl(path: Path):
    """Yield decoded JSON objects from a JSONL file; skip malformed lines."""
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _percentile(values: list[float], p: float) -> float:
    """Return the p-th percentile (0.0–1.0) of `values`.

    Uses nearest-rank method — deterministic and fine for small samples
    like daily metrics. Empty input returns 0.0.
    """
    if not values:
        return 0.0
    if p <= 0.0:
        return min(values)
    if p >= 1.0:
        return max(values)
    sorted_vals = sorted(values)
    # Nearest-rank: index = ceil(p * N) - 1 (clamped to [0, N-1])
    idx = max(0, min(len(sorted_vals) - 1, math.ceil(p * len(sorted_vals)) - 1))
    return sorted_vals[idx]
