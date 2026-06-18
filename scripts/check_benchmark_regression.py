#!/usr/bin/env python3
"""Benchmark regression gate — compares results against a stored baseline.

Exits 0 if all metrics are within threshold of the baseline.
Exits 1 if any metric regresses beyond the threshold.

Usage
-----
  python scripts/check_benchmark_regression.py \
      --results benchmark-results.json \
      --baseline tests/benchmarks/baseline.json \
      --threshold 0.20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Metrics checked for regression (higher is worse for all of them)
_REGRESSION_METRICS = ["build_ms", "p50_ms", "p95_ms"]


def check_regression(
    results: dict,
    baseline: dict,
    threshold: float,
) -> list[str]:
    """Return a list of failure messages (empty = pass)."""
    failures: list[str] = []
    for metric in _REGRESSION_METRICS:
        if metric not in baseline:
            print(f"  [skip] {metric}: not in baseline — skipping")
            continue
        if metric not in results:
            failures.append(f"{metric}: missing from results")
            continue

        current = float(results[metric])
        base = float(baseline[metric])
        if base <= 0:
            continue
        delta_pct = (current - base) / base
        status = "✓" if delta_pct <= threshold else "✗"
        print(
            f"  {status} {metric}: "
            f"{current:.2f}ms vs baseline {base:.2f}ms "
            f"({delta_pct:+.1%})"
        )
        if delta_pct > threshold:
            failures.append(
                f"{metric} regressed by {delta_pct:.1%} "
                f"(current={current:.2f}ms, baseline={base:.2f}ms, "
                f"threshold={threshold:.0%})"
            )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Check benchmark regression against baseline.")
    parser.add_argument("--results", type=Path, required=True, help="Current benchmark results JSON")
    parser.add_argument("--baseline", type=Path, required=True, help="Baseline benchmark JSON")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.20,
        help="Max allowed regression fraction (default: 0.20 = 20%%)",
    )
    args = parser.parse_args()

    if not args.results.exists():
        print(f"ERROR: results file not found: {args.results}", file=sys.stderr)
        sys.exit(1)
    if not args.baseline.exists():
        print(f"ERROR: baseline file not found: {args.baseline}", file=sys.stderr)
        sys.exit(1)

    results = json.loads(args.results.read_text())
    baseline = json.loads(args.baseline.read_text())

    print(f"\nRegression check (threshold: {args.threshold:.0%})")
    print(f"  Corpus: {results.get('n_docs', '?')} docs, {results.get('n_queries', '?')} queries")
    failures = check_regression(results, baseline, args.threshold)

    if failures:
        print("\nFAILED — performance regressions detected:")
        for msg in failures:
            print(f"  ✗ {msg}")
        print(
            "\nIf the regression is expected (e.g., new correctness work), "
            "update tests/benchmarks/baseline.json to reflect the new baseline."
        )
        sys.exit(1)
    else:
        print("\nPASSED — all metrics within threshold")
        sys.exit(0)


if __name__ == "__main__":
    main()
