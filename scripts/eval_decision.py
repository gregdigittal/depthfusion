#!/usr/bin/env python3
"""Decision-extraction measurement (S-64 target: S-45 AC-1 precision).

Runs `HeuristicDecisionExtractor` against the labelled gold set in
`docs/eval-sets/decision-extraction/` and reports precision.

Precision is computed by matching extracted decisions against the
`expected` list with a bag-of-words cosine threshold (default 0.5).
Loose matching is deliberate: the extractor paraphrases; we count a
decision as correct if it covers the same content as the expected
entry, not if the wording matches.

Usage:
    python scripts/eval_decision.py
    python scripts/eval_decision.py --include-seeds
    python scripts/eval_decision.py --single docs/eval-sets/decision-extraction/001-auth-migration.json
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
GOLD_DIR = PROJECT_ROOT / "docs" / "eval-sets" / "decision-extraction"


# --------------------------------------------------------------------------
# Bag-of-words cosine (no embedding backend needed for this pass)
# --------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


def tokenise(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def bow_cosine(a: str, b: str) -> float:
    """Cosine similarity of bag-of-words vectors. Returns 0.0 on empty input."""
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


# --------------------------------------------------------------------------
# Gold-set loading
# --------------------------------------------------------------------------

def load_gold(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema") != "decision-extraction/v1":
        raise ValueError(f"{path}: unexpected schema {data.get('schema')!r}")
    return data


def collect_gold_files(include_seeds: bool) -> list[Path]:
    files = sorted(GOLD_DIR.glob("*.json"))
    if include_seeds:
        files.extend(sorted((GOLD_DIR / "_seeds").glob("*.json")))
    return files


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

@dataclass
class MatchResult:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    per_file_diagnostics: list[str] | None = None

    def __post_init__(self) -> None:
        if self.per_file_diagnostics is None:
            self.per_file_diagnostics = []

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else float("nan")

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else float("nan")


def match_extracted_to_expected(
    extracted: list[str],
    expected: list[str],
    threshold: float,
) -> tuple[int, int, int, list[str]]:
    """Greedy 1-to-1 match. Returns (tp, fp, fn, diagnostics).

    Each extracted decision is matched against the best-scoring remaining
    expected entry above threshold. Unmatched extractions are FP; unmatched
    expected are FN.
    """
    diags: list[str] = []
    remaining_expected = list(expected)
    tp = 0
    fp = 0
    for ex in extracted:
        if not remaining_expected:
            fp += 1
            diags.append(f"  FP: extracted with no expected left: {ex[:80]!r}")
            continue
        scored = [(bow_cosine(ex, e), i) for i, e in enumerate(remaining_expected)]
        best_score, best_i = max(scored, key=lambda t: t[0])
        if best_score >= threshold:
            tp += 1
            del remaining_expected[best_i]
        else:
            fp += 1
            diags.append(f"  FP: best match was {best_score:.2f}: {ex[:80]!r}")

    for exp in remaining_expected:
        diags.append(f"  FN: expected but not extracted: {exp[:80]!r}")
    fn = len(remaining_expected)
    return tp, fp, fn, diags


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def compute_metrics(files: list[Path], threshold: float) -> MatchResult:
    # Import deferred: lets tests exercise the pure-function path without
    # requiring the full depthfusion package in the test env.
    from depthfusion.capture.decision_extractor import HeuristicDecisionExtractor

    extractor = HeuristicDecisionExtractor()
    result = MatchResult()

    for path in files:
        try:
            gold = load_gold(path)
        except (ValueError, json.JSONDecodeError) as err:
            print(f"SKIP {path.name}: {err}", file=sys.stderr)
            continue

        input_text = gold["input_text"]
        expected_texts = [e["text"] for e in gold.get("expected", [])]
        extracted_entries = extractor.extract(input_text, source_session=gold["source_session"])
        extracted_texts = [e.text for e in extracted_entries]

        tp, fp, fn, diags = match_extracted_to_expected(
            extracted_texts, expected_texts, threshold
        )
        result.tp += tp
        result.fp += fp
        result.fn += fn
        if diags:
            assert result.per_file_diagnostics is not None
            result.per_file_diagnostics.append(f"{path.name}:")
            result.per_file_diagnostics.extend(diags)

    return result


def format_report(result: MatchResult, n_files: int, threshold: float) -> str:
    lines = []
    lines.append("# Decision-Extraction Eval Report")
    lines.append("")
    lines.append(f"- Files evaluated: {n_files}")
    lines.append(f"- Match threshold (bag-of-words cosine): {threshold}")
    lines.append(f"- Extractor: HeuristicDecisionExtractor")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| True positives  | {result.tp} |")
    lines.append(f"| False positives | {result.fp} |")
    lines.append(f"| False negatives | {result.fn} |")
    prec = result.precision
    rec = result.recall
    lines.append(f"| Precision | {prec:.3f} |")
    lines.append(f"| Recall    | {rec:.3f} |")
    lines.append("")
    lines.append(f"**S-45 AC-1 target:** precision ≥ 0.80.")
    if not math.isnan(prec):
        status = "✅ PASS" if prec >= 0.80 else "❌ FAIL"
        lines.append(f"**Current:** {status} ({prec:.3f})")
    lines.append("")
    if result.per_file_diagnostics:
        lines.append("## Per-file diagnostics")
        lines.append("")
        lines.append("```")
        lines.extend(result.per_file_diagnostics)
        lines.append("```")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decision-extraction measurement")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Bag-of-words cosine threshold for match (default 0.5)")
    parser.add_argument("--include-seeds", action="store_true",
                        help="Include _seeds/ files (normally excluded)")
    parser.add_argument("--single", help="Measure a single file instead of the full set")
    args = parser.parse_args(argv)

    if args.single:
        files = [Path(args.single)]
    else:
        files = collect_gold_files(args.include_seeds)
    if not files:
        print("No gold files found. Populate docs/eval-sets/decision-extraction/ or use --include-seeds.",
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
