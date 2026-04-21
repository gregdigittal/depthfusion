#!/usr/bin/env python3
"""CIQS summarisation (S-63 T-200).

Reads N scored JSONL files produced by `ciqs_harness.py score` and
emits a markdown summary with per-category mean, stddev, and bootstrap
95% CI. This is the final stage of the three-step benchmark flow:

    ciqs_harness.py run   -> raw.jsonl + scoring.md
    ciqs_harness.py score -> scored.jsonl   (operator fills in scores)
    ciqs_summarise.py     -> summary.md     (aggregates N scored runs)

Usage:
    python scripts/ciqs_summarise.py \\
        --mode local \\
        docs/benchmarks/2026-04-21-local-run1-scored.jsonl \\
        docs/benchmarks/2026-04-22-local-run2-scored.jsonl \\
        docs/benchmarks/2026-04-23-local-run3-scored.jsonl \\
        --out docs/benchmarks/2026-04-23-local-summary.md

Math:
  * mean, stddev: per-category normalised scores (score / max * 100)
  * bootstrap CI: 5000 resamples, 2.5/97.5 percentile.
    (Bootstrap is chosen because with 3-5 runs we cannot assume
    normality and the classical t-CI is too narrow.)
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Iterable


# --------------------------------------------------------------------------
# Math helpers (pure functions, directly tested)
# --------------------------------------------------------------------------

def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile in [0, 100]. Returns NaN on empty input."""
    if not values:
        return float("nan")
    if len(values) == 1:
        return values[0]
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"p must be in [0, 100], got {p}")
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lower = int(math.floor(k))
    upper = int(math.ceil(k))
    if lower == upper:
        return sorted_vals[lower]
    fraction = k - lower
    return sorted_vals[lower] + fraction * (sorted_vals[upper] - sorted_vals[lower])


def bootstrap_ci(
    values: list[float],
    confidence: float = 0.95,
    n_resamples: int = 5000,
    seed: int | None = 1729,
) -> tuple[float, float]:
    """Bootstrap CI for the mean. Returns (low, high).

    On empty input returns (nan, nan). On a single-value input the CI is
    collapsed to (value, value) since bootstrapping a single point is
    degenerate but not an error.
    """
    if not values:
        return float("nan"), float("nan")
    if len(values) == 1:
        return values[0], values[0]
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")

    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(n_resamples):
        sample = [values[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)

    tail = (1.0 - confidence) / 2.0
    low = percentile(means, tail * 100.0)
    high = percentile(means, (1.0 - tail) * 100.0)
    return low, high


def normalise_topic_score(dim_scores: dict[str, int], max_score: int) -> float:
    """Normalise a topic's dim scores to [0, 100]."""
    if max_score <= 0:
        return 0.0
    return (sum(dim_scores.values()) / max_score) * 100.0


# --------------------------------------------------------------------------
# JSONL loading
# --------------------------------------------------------------------------

def load_scored(paths: Iterable[Path]) -> list[dict]:
    records: list[dict] = []
    for p in paths:
        if not p.exists():
            print(f"WARNING: skipping missing file {p}", file=sys.stderr)
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def group_by_category(records: list[dict]) -> dict[str, list[float]]:
    """Return {category_id: [normalised_score_per_topic_run, ...]}.

    A normalised score is sum(rubric dim scores) / max * 100. Category A
    and E have different max scores; we read max by inferring from the
    rubric dims count × 10 (10 = max per dim). This is a defensive
    calculation — if a scoring template had fewer dims populated than
    the battery declared, this under-normalises. A clean pipeline
    produces all dims.
    """
    by_cat: dict[str, list[float]] = {}
    for rec in records:
        scores = rec.get("scores")
        if not scores:
            continue
        cat = rec["category_id"]
        # max = 10 per dim × number of dims filled
        max_score = 10 * len(scores)
        by_cat.setdefault(cat, []).append(normalise_topic_score(scores, max_score))
    return by_cat


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def format_report(
    per_cat: dict[str, list[float]],
    mode: str,
    source_files: list[Path],
    confidence: float = 0.95,
) -> str:
    lines: list[str] = []
    lines.append(f"# CIQS Summary - {mode}")
    lines.append("")
    lines.append(f"> Source runs: {len(source_files)}")
    for p in source_files:
        lines.append(f"> - `{p}`")
    lines.append(f"> Confidence level: {int(confidence * 100)}%")
    lines.append("")
    lines.append("## Per-category statistics")
    lines.append("")
    ci_pct = int(round(confidence * 100))
    lines.append(f"| Category | N | Mean | Stddev | {ci_pct}% CI (bootstrap) |")
    lines.append("|----------|---|------|--------|--------------------|")

    overall_means: list[float] = []
    for cat in sorted(per_cat.keys()):
        vals = per_cat[cat]
        n = len(vals)
        if n == 0:
            continue
        mean = statistics.fmean(vals)
        sd = statistics.stdev(vals) if n > 1 else 0.0
        low, high = bootstrap_ci(vals, confidence=confidence)
        lines.append(
            f"| {cat} | {n} | {mean:.1f} | {sd:.1f} | [{low:.1f}, {high:.1f}] |"
        )
        overall_means.append(mean)

    lines.append("")
    if overall_means:
        grand = sum(overall_means) / len(overall_means)
        lines.append(f"**Unweighted composite (category means averaged):** {grand:.1f}")
        lines.append("")
        lines.append("> Note: the weighted composite per the battery's `composite_weights`")
        lines.append("> is not computed here because weights change with the battery version.")
        lines.append("> Compute it in a follow-up cell if needed.")
    lines.append("")

    lines.append("## Raw normalised scores")
    lines.append("")
    for cat in sorted(per_cat.keys()):
        vals = per_cat[cat]
        lines.append(f"- {cat}: {[round(v, 1) for v in vals]}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CIQS summariser")
    parser.add_argument("files", nargs="+", help="Scored JSONL files (output of ciqs_harness.py score)")
    parser.add_argument("--mode", required=True, help="Mode label for the report header")
    parser.add_argument("--out", help="Output markdown path (default: stdout)")
    parser.add_argument("--confidence", type=float, default=0.95)
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.files]
    records = load_scored(paths)
    if not records:
        print("ERROR: no records loaded", file=sys.stderr)
        return 2

    per_cat = group_by_category(records)
    if not per_cat:
        print("ERROR: no scored records found (did you run `ciqs_harness.py score` yet?)",
              file=sys.stderr)
        return 2

    report = format_report(per_cat, args.mode, paths, confidence=args.confidence)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
