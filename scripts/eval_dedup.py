#!/usr/bin/env python3
"""Dedup measurement (S-64 target: S-49 AC-2 false-dedup rate).

Runs bag-of-words cosine against the labelled gold pairs in
`docs/eval-sets/dedup/` and reports false-dedup rate.

A pair is "flagged as duplicate" if `bow_cosine(a, b) >= threshold`.

  * False-dedup rate = |false-dup pairs flagged| / |false-dup pairs|
  * True-dedup hit rate = |true-dup pairs flagged| / |true-dup pairs|

Note: the production dedup code (`depthfusion.capture.dedup`) uses
embedding-space cosine, not bag-of-words. This script uses BOW because
it's backend-free. A v2 flag `--cosine=embedding` would exercise the
production path; deferred until gold-set population stabilises.

Usage:
    python scripts/eval_dedup.py
    python scripts/eval_dedup.py --include-seeds
    python scripts/eval_dedup.py --threshold 0.90  # explore sensitivity
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = PROJECT_ROOT / "docs" / "eval-sets" / "dedup"


_WORD = re.compile(r"[a-z0-9]+")


def tokenise(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def bow_cosine(a: str, b: str) -> float:
    ta = tokenise(a)
    tb = tokenise(b)
    if not ta or not tb:
        return 0.0
    from collections import Counter
    ca = Counter(ta)
    cb = Counter(tb)
    shared = set(ca) & set(cb)
    dot = sum(ca[w] * cb[w] for w in shared)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def load_gold(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema") != "dedup/v1":
        raise ValueError(f"{path}: unexpected schema {data.get('schema')!r}")
    if data.get("label") not in {"true-dup", "false-dup"}:
        raise ValueError(f"{path}: unexpected label {data.get('label')!r}")
    return data


def collect_gold_files(include_seeds: bool) -> list[Path]:
    files = sorted(GOLD_DIR.glob("*.json"))
    if include_seeds:
        files.extend(sorted((GOLD_DIR / "_seeds").glob("*.json")))
    return files


@dataclass
class DedupResult:
    true_dups_flagged: int = 0
    true_dups_total: int = 0
    false_dups_flagged: int = 0
    false_dups_total: int = 0
    per_pair: list[tuple[str, str, float, bool]] | None = None  # (name, label, score, flagged)

    def __post_init__(self) -> None:
        if self.per_pair is None:
            self.per_pair = []

    @property
    def false_dedup_rate(self) -> float:
        if self.false_dups_total == 0:
            return float("nan")
        return self.false_dups_flagged / self.false_dups_total

    @property
    def true_dedup_hit_rate(self) -> float:
        if self.true_dups_total == 0:
            return float("nan")
        return self.true_dups_flagged / self.true_dups_total


def compute_metrics(files: list[Path], threshold: float) -> DedupResult:
    result = DedupResult()
    for path in files:
        try:
            gold = load_gold(path)
        except (ValueError, json.JSONDecodeError) as err:
            print(f"SKIP {path.name}: {err}", file=sys.stderr)
            continue

        score = bow_cosine(gold["a"], gold["b"])
        flagged = score >= threshold
        label = gold["label"]
        assert result.per_pair is not None
        result.per_pair.append((path.name, label, score, flagged))

        if label == "true-dup":
            result.true_dups_total += 1
            if flagged:
                result.true_dups_flagged += 1
        else:  # false-dup
            result.false_dups_total += 1
            if flagged:
                result.false_dups_flagged += 1
    return result


def format_report(result: DedupResult, n_files: int, threshold: float) -> str:
    lines = []
    lines.append("# Dedup Eval Report")
    lines.append("")
    lines.append(f"- Pairs evaluated: {n_files}")
    lines.append(f"- Cosine threshold: {threshold}")
    lines.append(f"- Cosine variant: bag-of-words (backend-free)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Label | Flagged / Total |")
    lines.append("|-------|------------------|")
    lines.append(f"| true-dup  | {result.true_dups_flagged} / {result.true_dups_total} |")
    lines.append(f"| false-dup | {result.false_dups_flagged} / {result.false_dups_total} |")
    lines.append("")
    fdr = result.false_dedup_rate
    tdhr = result.true_dedup_hit_rate
    lines.append(f"- False-dedup rate: {fdr:.3f}")
    lines.append(f"- True-dedup hit rate: {tdhr:.3f}")
    lines.append("")
    lines.append(f"**S-49 AC-2 target:** false-dedup rate ≤ 0.05.")
    if not math.isnan(fdr):
        status = "✅ PASS" if fdr <= 0.05 else "❌ FAIL"
        lines.append(f"**Current:** {status} ({fdr:.3f})")
    lines.append("")
    if result.per_pair:
        lines.append("## Per-pair scores")
        lines.append("")
        lines.append("| File | Label | Cosine | Flagged? |")
        lines.append("|------|-------|--------|----------|")
        assert result.per_pair is not None
        for name, label, score, flagged in result.per_pair:
            mark = "✓" if flagged else "·"
            lines.append(f"| {name} | {label} | {score:.3f} | {mark} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dedup measurement")
    parser.add_argument("--threshold", type=float, default=0.92,
                        help="Cosine threshold (default 0.92 matches production)")
    parser.add_argument("--include-seeds", action="store_true",
                        help="Include _seeds/ files")
    args = parser.parse_args(argv)

    files = collect_gold_files(args.include_seeds)
    if not files:
        print("No gold files. Populate docs/eval-sets/dedup/ or use --include-seeds.",
              file=sys.stderr)
        return 1

    result = compute_metrics(files, args.threshold)
    print(format_report(result, len(files), args.threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
