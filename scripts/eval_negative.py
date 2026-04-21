#!/usr/bin/env python3
"""Negative-signal measurement (S-64 target: S-48 AC-2 false-negative rate).

Runs `HeuristicNegativeExtractor` against the labelled gold set in
`docs/eval-sets/negative/` and reports false-negative rate:

    FNR = |genuine negatives missed| / |genuine negatives|

For `positive` examples (where `expected_type: "positive"`), any
extraction is a false-positive and is reported separately.

Usage:
    python scripts/eval_negative.py
    python scripts/eval_negative.py --include-seeds
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = PROJECT_ROOT / "docs" / "eval-sets" / "negative"


_WORD = re.compile(r"[a-z0-9]+")


def tokenise(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def bow_cosine(a: str, b: str) -> float:
    ta = tokenise(a)
    tb = tokenise(b)
    if not ta or not tb:
        return 0.0
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
    if data.get("schema") != "negative/v1":
        raise ValueError(f"{path}: unexpected schema {data.get('schema')!r}")
    if data.get("expected_type") not in {"negative", "positive"}:
        raise ValueError(f"{path}: unexpected expected_type {data.get('expected_type')!r}")
    return data


def collect_gold_files(include_seeds: bool) -> list[Path]:
    files = sorted(GOLD_DIR.glob("*.json"))
    if include_seeds:
        files.extend(sorted((GOLD_DIR / "_seeds").glob("*.json")))
    return files


@dataclass
class NegativeResult:
    genuine_total: int = 0
    genuine_found: int = 0
    false_positive_count: int = 0  # extractions on `positive` files
    positive_files: int = 0
    per_file: list[str] | None = None

    def __post_init__(self) -> None:
        if self.per_file is None:
            self.per_file = []

    @property
    def false_negative_rate(self) -> float:
        if self.genuine_total == 0:
            return float("nan")
        return 1.0 - (self.genuine_found / self.genuine_total)


def _best_match(extracted_what_list: list[str], target: str, threshold: float) -> float:
    if not extracted_what_list:
        return 0.0
    return max(bow_cosine(x, target) for x in extracted_what_list)


def compute_metrics(files: list[Path], threshold: float) -> NegativeResult:
    # Import deferred: same rationale as eval_decision.compute_metrics.
    from depthfusion.capture.negative_extractor import HeuristicNegativeExtractor

    extractor = HeuristicNegativeExtractor()
    result = NegativeResult()

    for path in files:
        try:
            gold = load_gold(path)
        except (ValueError, json.JSONDecodeError) as err:
            print(f"SKIP {path.name}: {err}", file=sys.stderr)
            continue

        extracted = extractor.extract(gold["input_text"], source_session=gold["source_session"])
        extracted_whats = [e.what for e in extracted]

        if gold["expected_type"] == "positive":
            result.positive_files += 1
            if extracted:
                result.false_positive_count += len(extracted)
                assert result.per_file is not None
                result.per_file.append(
                    f"{path.name}: FP — extractor produced {len(extracted)} on positive text"
                )
            continue

        for exp in gold.get("expected_negatives", []):
            result.genuine_total += 1
            best = _best_match(extracted_whats, exp["what"], threshold)
            if best >= threshold:
                result.genuine_found += 1
            else:
                assert result.per_file is not None
                result.per_file.append(
                    f"{path.name}: MISSED — expected {exp['what'][:60]!r} "
                    f"(best cosine {best:.2f} vs threshold {threshold})"
                )

    return result


def format_report(result: NegativeResult, n_files: int, threshold: float) -> str:
    lines = []
    lines.append("# Negative-Signal Eval Report")
    lines.append("")
    lines.append(f"- Files evaluated: {n_files}")
    lines.append(f"- Cosine threshold for match: {threshold}")
    lines.append(f"- Extractor: HeuristicNegativeExtractor")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Genuine negatives total   | {result.genuine_total} |")
    lines.append(f"| Genuine negatives found   | {result.genuine_found} |")
    lines.append(f"| Positive files            | {result.positive_files} |")
    lines.append(f"| False positives (extractions on positive files) | {result.false_positive_count} |")
    fnr = result.false_negative_rate
    lines.append(f"| False-negative rate       | {fnr:.3f} |")
    lines.append("")
    lines.append("**S-48 AC-2 target:** false-negative rate ≤ 0.10.")
    if not math.isnan(fnr):
        status = "✅ PASS" if fnr <= 0.10 else "❌ FAIL"
        lines.append(f"**Current:** {status} ({fnr:.3f})")
    lines.append("")
    if result.per_file:
        lines.append("## Per-file diagnostics")
        lines.append("")
        lines.append("```")
        assert result.per_file is not None
        lines.extend(result.per_file)
        lines.append("```")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Negative-signal measurement")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Bag-of-words cosine threshold for match (default 0.5)")
    parser.add_argument("--include-seeds", action="store_true",
                        help="Include _seeds/ files")
    args = parser.parse_args(argv)

    files = collect_gold_files(args.include_seeds)
    if not files:
        print("No gold files. Populate docs/eval-sets/negative/ or use --include-seeds.",
              file=sys.stderr)
        return 1

    try:
        result = compute_metrics(files, args.threshold)
    except ImportError as err:
        print(f"ERROR: cannot import extractor: {err}", file=sys.stderr)
        return 2
    print(format_report(result, len(files), args.threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
