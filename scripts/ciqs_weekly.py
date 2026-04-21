#!/usr/bin/env python3
"""Weekly autonomous regression monitor for DepthFusion metrics.

Reads the structured JSONL streams emitted in production (recall,
capture, and gate logs), aggregates the last 7 days, compares to the
previous 7 days, and writes a markdown report flagging regressions.

Design note — why NOT auto-score quality:
  Category-A quality regression requires labelled expected-output sets
  (which prompt retrieved which files). Without that ground truth, we
  can't distinguish "the retrieval is worse" from "the corpus changed
  content". So the autonomous runner sticks to PURELY MECHANICAL
  signals (latency, error rate, availability, write volume) that need
  no labels and produce no false-positives from corpus drift.

  Quality regression stays in the human-in-loop monthly cadence driven
  by `ciqs_harness.py` with operator scoring.

Regression thresholds (configurable via CLI):
  * Backend p95 latency: +20% vs baseline week → warn
  * Backend error_rate:  +5 percentage points  → warn
  * Capture volume:      -30% vs baseline week → warn (less data captured
                                                 usually means something
                                                 is broken upstream)
  * Backend availability:< 95% over the week   → warn

Exit codes:
  0 = no regressions detected
  1 = regressions detected (report written)
  2 = error running analysis (no report produced)

Usage (one-shot):
    python scripts/ciqs_weekly.py --out docs/benchmarks/weekly/$(date +%F).md

Usage (scheduled via systemd timer — see scripts/ciqs-weekly.timer):
    Installed weekly; writes to
    ~/.local/share/depthfusion/weekly-reports/<date>.md and prints
    summary to stdout (captured by systemd journal).

Spec: S-63 autonomous-execution extension + observability follow-up
from the v0.7 roadmap §1a. No labels required; no CIQS harness
required; pure aggregation over data we already emit.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Window aggregation
# --------------------------------------------------------------------------

def collect_window(
    aggregator: Any,
    end_date: date,
    window_days: int = 7,
) -> dict[str, Any]:
    """Aggregate N days ending on end_date (inclusive).

    Returns:
      {
        "per_backend": {
          "<cap>::<backend>": {
             "count": int, "avg_latency_ms": float,
             "p95_ms_samples": [float, ...],   # one per day
             "error_rate_samples": [float, ...],
             "days_observed": int,
          },
          ...
        },
        "total_queries": int,
        "total_errors": int,
        "overall_error_rate": float,
        "captures_by_mechanism": {"<mechanism>": int_total_writes, ...},
        "capture_write_total": int,
        "days_with_recall_data": int,
      }
    """
    per_backend: dict[str, dict] = {}
    total_queries = 0
    total_errors = 0
    days_with_recall_data = 0
    captures_by_mechanism: dict[str, int] = {}
    capture_write_total = 0

    for i in range(window_days):
        d = end_date - timedelta(days=i)

        backend_s = aggregator.backend_summary(d)
        if backend_s:
            days_with_recall_data += 1
            total_queries += backend_s.get("total_queries", 0)
            total_errors += backend_s.get("total_errors", 0)
            for key, stats in backend_s.get("per_backend", {}).items():
                slot = per_backend.setdefault(key, {
                    "count": 0,
                    "sum_latency": 0.0,
                    "measured_count": 0,
                    "p95_ms_samples": [],
                    "error_rate_samples": [],
                    "days_observed": 0,
                    "error_count": 0,
                })
                slot["count"] += stats.get("count", 0)
                slot["error_count"] += stats.get("error_count", 0)
                measured = stats.get("measured_count", 0)
                slot["measured_count"] += measured
                slot["sum_latency"] += stats.get("avg_latency_ms", 0.0) * measured
                if stats.get("p95_latency_ms", 0.0) > 0:
                    slot["p95_ms_samples"].append(stats["p95_latency_ms"])
                slot["error_rate_samples"].append(stats.get("error_rate", 0.0))
                slot["days_observed"] += 1

        capture_s = aggregator.capture_summary(d)
        if capture_s:
            for mech, stats in capture_s.get("per_mechanism", {}).items():
                writes = stats.get("total", 0)
                captures_by_mechanism[mech] = captures_by_mechanism.get(mech, 0) + writes
                capture_write_total += writes

    # Finalize per-backend stats
    for key, slot in per_backend.items():
        mc = slot["measured_count"]
        slot["avg_latency_ms"] = slot["sum_latency"] / mc if mc else 0.0
        slot["p95_ms_avg"] = (
            sum(slot["p95_ms_samples"]) / len(slot["p95_ms_samples"])
            if slot["p95_ms_samples"] else 0.0
        )
        # Per-day error rate averaged (prevents one bad day from
        # dominating if total volume varied massively day to day)
        slot["error_rate_avg"] = (
            sum(slot["error_rate_samples"]) / len(slot["error_rate_samples"])
            if slot["error_rate_samples"] else 0.0
        )
        # Strip intermediate fields
        del slot["sum_latency"]

    return {
        "per_backend": per_backend,
        "total_queries": total_queries,
        "total_errors": total_errors,
        "overall_error_rate": total_errors / total_queries if total_queries else 0.0,
        "captures_by_mechanism": captures_by_mechanism,
        "capture_write_total": capture_write_total,
        "days_with_recall_data": days_with_recall_data,
        # Store window_days in the dict so downstream consumers (detect_regressions,
        # format_report) compute availability against the real window size rather
        # than hardcoding / 7 — fixes review-gate H-2.
        "window_days": window_days,
    }


# --------------------------------------------------------------------------
# Regression detection
# --------------------------------------------------------------------------

def detect_regressions(
    current: dict,
    baseline: dict,
    latency_pct_threshold: float = 0.20,
    error_rate_pp_threshold: float = 0.05,
    capture_volume_pct_threshold: float = 0.30,
    availability_threshold: float = 0.95,
) -> list[dict]:
    """Compare two aggregated windows; return list of regression findings.

    Each finding:
      {
        "kind": "latency" | "error_rate" | "capture_volume" | "availability",
        "subject": "gemma::reranker" or "capture total" etc.,
        "baseline": <number>,
        "current": <number>,
        "delta": <number>,
        "threshold": <number>,
        "severity": "warn" | "alert",
      }
    """
    findings: list[dict] = []

    # Per-backend latency + error-rate regression
    for key, cur in current["per_backend"].items():
        base = baseline["per_backend"].get(key)
        if not base:
            continue  # new backend this week; nothing to compare

        # Latency: p95_ms_avg up > threshold_pct
        cur_p95 = cur.get("p95_ms_avg", 0.0)
        base_p95 = base.get("p95_ms_avg", 0.0)
        if base_p95 > 0 and cur_p95 > 0:
            pct = (cur_p95 - base_p95) / base_p95
            if pct > latency_pct_threshold:
                findings.append({
                    "kind": "latency",
                    "subject": key,
                    "baseline": round(base_p95, 1),
                    "current": round(cur_p95, 1),
                    "delta": f"+{pct*100:.1f}%",
                    "threshold": f"+{latency_pct_threshold*100:.0f}%",
                    "severity": "alert" if pct > 2 * latency_pct_threshold else "warn",
                })

        # Error rate: absolute pp increase
        cur_err = cur.get("error_rate_avg", 0.0)
        base_err = base.get("error_rate_avg", 0.0)
        delta_pp = cur_err - base_err
        if delta_pp > error_rate_pp_threshold:
            findings.append({
                "kind": "error_rate",
                "subject": key,
                "baseline": f"{base_err*100:.1f}%",
                "current": f"{cur_err*100:.1f}%",
                "delta": f"+{delta_pp*100:.1f}pp",
                "threshold": f"+{error_rate_pp_threshold*100:.0f}pp",
                "severity": "alert" if delta_pp > 2 * error_rate_pp_threshold else "warn",
            })

    # Capture volume regression
    cur_cap = current["capture_write_total"]
    base_cap = baseline["capture_write_total"]
    if base_cap > 0:
        pct = (cur_cap - base_cap) / base_cap  # negative = drop
        if pct < -capture_volume_pct_threshold:
            findings.append({
                "kind": "capture_volume",
                "subject": "total",
                "baseline": base_cap,
                "current": cur_cap,
                "delta": f"{pct*100:.1f}%",
                "threshold": f"-{capture_volume_pct_threshold*100:.0f}%",
                "severity": "alert" if pct < -2 * capture_volume_pct_threshold else "warn",
            })

    # Availability (days_with_recall_data / window_days)
    # Interpreted as: if this week had data for < availability_threshold
    # of days and last week had full coverage, that's a drop worth flagging.
    # H-2 fix: divide by the actual window_days from each dict, not hardcoded 7,
    # so --window-days 14 produces correct ratios in [0, 1].
    cur_window = current.get("window_days", 7)
    base_window = baseline.get("window_days", 7)
    cur_avail = current["days_with_recall_data"] / cur_window if cur_window else 0.0
    base_avail = baseline["days_with_recall_data"] / base_window if base_window else 0.0
    if cur_avail < availability_threshold and base_avail >= availability_threshold:
        findings.append({
            "kind": "availability",
            "subject": "recall stream",
            "baseline": f"{base_avail*100:.0f}%",
            "current": f"{cur_avail*100:.0f}%",
            "delta": f"{(cur_avail-base_avail)*100:.0f}pp",
            "threshold": f"≥{availability_threshold*100:.0f}%",
            "severity": "alert",
        })

    return findings


# --------------------------------------------------------------------------
# Report formatting
# --------------------------------------------------------------------------

def format_report(
    current: dict,
    baseline: dict,
    findings: list[dict],
    current_end: date,
    window_days: int = 7,
) -> str:
    cur_start = current_end - timedelta(days=window_days - 1)
    base_start = cur_start - timedelta(days=window_days)
    base_end = cur_start - timedelta(days=1)

    lines: list[str] = []
    lines.append(f"# Weekly Metrics Report — {cur_start} to {current_end}")
    lines.append("")
    lines.append(f"Baseline window: {base_start} to {base_end} "
                 f"({window_days} days each)")
    lines.append("")
    if findings:
        alerts = [f for f in findings if f["severity"] == "alert"]
        warns = [f for f in findings if f["severity"] == "warn"]
        lines.append(
            f"**⚠ {len(findings)} regression(s) detected** "
            f"({len(alerts)} alert, {len(warns)} warn)"
        )
    else:
        lines.append("**✓ No regressions detected.**")
    lines.append("")

    # Regressions
    if findings:
        lines.append("## Regressions")
        lines.append("")
        lines.append("| Kind | Subject | Baseline | Current | Δ | Threshold | Severity |")
        lines.append("|------|---------|----------|---------|---|-----------|----------|")
        for f in findings:
            lines.append(
                f"| {f['kind']} | `{f['subject']}` | {f['baseline']} | "
                f"{f['current']} | {f['delta']} | {f['threshold']} | "
                f"**{f['severity']}** |"
            )
        lines.append("")

    # Current week summary
    lines.append("## Current week — backend latency + errors")
    lines.append("")
    if current["per_backend"]:
        lines.append("| Backend | Queries | Errors | Err rate | p95 ms (avg of daily p95s) |")
        lines.append("|---------|---------|--------|----------|----------------------------|")
        for key in sorted(current["per_backend"].keys()):
            stats = current["per_backend"][key]
            lines.append(
                f"| `{key}` | {stats['count']} | {stats['error_count']} | "
                f"{stats['error_rate_avg']*100:.1f}% | {stats['p95_ms_avg']:.1f} |"
            )
        lines.append("")
    else:
        lines.append("*No backend data for the current window.*")
        lines.append("")

    # Capture volume
    lines.append("## Current week — capture volume")
    lines.append("")
    lines.append(
        f"- Total capture writes: **{current['capture_write_total']}** "
        f"(baseline week: {baseline['capture_write_total']})"
    )
    if current["captures_by_mechanism"]:
        lines.append("- By mechanism:")
        for mech in sorted(current["captures_by_mechanism"].keys()):
            lines.append(
                f"  - `{mech}`: {current['captures_by_mechanism'][mech]} "
                f"(baseline: {baseline['captures_by_mechanism'].get(mech, 0)})"
            )
    lines.append("")

    # Availability — use the window_days stored in each dict rather
    # than hardcoded 7 (L-6 fix). Fallback to 7 only when the key is
    # absent (e.g., legacy test fixtures that construct dicts directly).
    cur_window_disp = current.get("window_days", 7)
    base_window_disp = baseline.get("window_days", 7)
    lines.append(
        f"## Observability coverage\n\n"
        f"- Current: recall stream present on {current['days_with_recall_data']}/{cur_window_disp} days\n"
        f"- Baseline: recall stream present on {baseline['days_with_recall_data']}/{base_window_disp} days\n"
    )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> **Note:** this report is mechanical — latency, errors, volume, "
        "availability. Quality regressions (did the retrieval return the "
        "right things?) are NOT detected here; they require labelled "
        "expected outputs. Run `ciqs_harness.py` with operator scoring "
        "for quality signal."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Weekly autonomous regression monitor for DepthFusion metrics"
    )
    parser.add_argument(
        "--end-date",
        help="Last day of the current window (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--window-days", type=int, default=7,
        help="Size of each comparison window in days (default: 7)",
    )
    parser.add_argument(
        "--latency-threshold", type=float, default=0.20,
        help="p95 latency increase flagged above this fraction (default: 0.20 = 20%%)",
    )
    parser.add_argument(
        "--error-threshold", type=float, default=0.05,
        help="Error rate increase flagged above this absolute (default: 0.05 = 5pp)",
    )
    parser.add_argument(
        "--capture-threshold", type=float, default=0.30,
        help="Capture volume drop flagged above this fraction (default: 0.30 = 30%%)",
    )
    parser.add_argument(
        "--out", type=Path,
        help="Output markdown path. Default: stdout.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    if args.end_date:
        try:
            end_date = date.fromisoformat(args.end_date)
        except ValueError:
            print(f"ERROR: invalid --end-date {args.end_date!r}", file=sys.stderr)
            return 2
    else:
        end_date = date.today()

    # Import at call time so tests can monkey-patch MetricsCollector
    # without paying an import-time fs scan.
    try:
        from depthfusion.metrics.collector import MetricsCollector
        from depthfusion.metrics.aggregator import MetricsAggregator
    except ImportError as exc:
        print(f"ERROR: could not import depthfusion.metrics: {exc}", file=sys.stderr)
        return 2

    agg = MetricsAggregator(MetricsCollector())
    current = collect_window(agg, end_date, args.window_days)
    baseline_end = end_date - timedelta(days=args.window_days)
    baseline = collect_window(agg, baseline_end, args.window_days)

    findings = detect_regressions(
        current, baseline,
        latency_pct_threshold=args.latency_threshold,
        error_rate_pp_threshold=args.error_threshold,
        capture_volume_pct_threshold=args.capture_threshold,
    )

    report = format_report(current, baseline, findings, end_date, args.window_days)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(report)

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
