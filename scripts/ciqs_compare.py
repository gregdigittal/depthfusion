#!/usr/bin/env python3
"""CIQS two-mode comparison (S-66 T-206 support).

Compares two sets of scored CIQS runs (e.g. pre-migration vs
post-migration, or vps-cpu vs vps-gpu) and produces a delta report
with bootstrap confidence intervals on the category-wise differences.

Usage:
    python scripts/ciqs_compare.py \\
        --baseline-label "vps-cpu (pre-migration)" \\
        --baseline docs/benchmarks/2026-04-21-vps-cpu-run{1,2,3}-scored.jsonl \\
        --candidate-label "vps-gpu (post-migration)" \\
        --candidate docs/benchmarks/2026-05-05-vps-gpu-run{1,2,3}-scored.jsonl \\
        --out docs/benchmarks/2026-05-05-vps-cpu-vs-vps-gpu.md

Statistics:
  * Per-category: mean for each set, delta = candidate_mean - baseline_mean
  * Bootstrap CI on the delta: 5000 resamples per side, independent
    (NOT paired — runs on different hosts don't have prompt-level
    pairing beyond topic identity). Same seed/method as the single-mode
    summariser for reproducibility.
  * Verdict per category:
      - `improved`  when CI_low > 0
      - `regressed` when CI_high < 0
      - `parity`    when 0 ∈ [CI_low, CI_high]

The verdict distinguishes "the delta is real" from "the delta could be
noise" — necessary because with 3-5 runs per side, small absolute
deltas often span zero in the CI and should not be claimed as wins.

Spec: closes S-43 AC-2, S-43 AC-3, S-44 AC-2, S-66 AC-1 reporting
requirements. Doesn't replace `ciqs_summarise.py` for single-mode
reports — both coexist, pick per need.
"""
from __future__ import annotations

import argparse
import importlib.util
import random
import statistics
import sys
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# Import shared helpers from ciqs_summarise.py
# --------------------------------------------------------------------------
#
# The two scripts live side-by-side in scripts/; we reuse math + IO
# helpers via importlib to avoid duplicating ~80 lines. Same pattern the
# unit tests use to load the summariser as a module.

_SUMMARISE_PATH = Path(__file__).parent / "ciqs_summarise.py"


def _load_summarise_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "ciqs_summarise_for_compare", _SUMMARISE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module spec from {_SUMMARISE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Lazy-init so import-time cost stays low and tests can monkeypatch.
_summ: Any = None


def _reset_summ_for_testing() -> None:
    """Clear the cached ciqs_summarise module reference.

    Exposed so future tests that want to inject a fake ciqs_summarise
    (e.g. to exercise error paths in bootstrap_delta_ci without
    involving the real percentile() / load_scored()) can do so
    reliably. Without this hook the lazy-cached module reference
    sticks for the lifetime of the test process. M-3 review-gate fix.
    """
    global _summ
    _summ = None


def _get_summ() -> Any:
    global _summ
    if _summ is None:
        _summ = _load_summarise_module()
    return _summ


# --------------------------------------------------------------------------
# Delta statistics
# --------------------------------------------------------------------------

def bootstrap_delta_ci(
    baseline: list[float],
    candidate: list[float],
    confidence: float = 0.95,
    n_resamples: int = 5000,
    seed: int | None = 1729,
) -> tuple[float, float]:
    """Bootstrap CI for the difference of means (candidate - baseline).

    Unpaired resampling: baseline and candidate are resampled
    independently each iteration. Appropriate when the two sets are
    from different physical runs (different hosts, different days) and
    there's no natural pairing beyond the aggregation level.

    On either empty input returns (nan, nan).
    On single-element-both inputs collapses the CI to the exact delta.
    """
    if not baseline or not candidate:
        return float("nan"), float("nan")
    if len(baseline) == 1 and len(candidate) == 1:
        d = candidate[0] - baseline[0]
        return d, d
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")

    rng = random.Random(seed)
    nb = len(baseline)
    nc = len(candidate)
    deltas: list[float] = []
    for _ in range(n_resamples):
        b_sample = [baseline[rng.randrange(nb)] for _ in range(nb)]
        c_sample = [candidate[rng.randrange(nc)] for _ in range(nc)]
        deltas.append(statistics.fmean(c_sample) - statistics.fmean(b_sample))

    alpha = (1 - confidence) / 2
    low_p = alpha * 100
    high_p = (1 - alpha) * 100
    summ = _get_summ()
    return summ.percentile(deltas, low_p), summ.percentile(deltas, high_p)


def classify_delta(low: float, high: float) -> str:
    """Verdict for a category based on the delta CI spanning zero or not."""
    if low != low or high != high:  # NaN check
        return "insufficient-data"
    if low > 0:
        return "improved"
    if high < 0:
        return "regressed"
    return "parity"


# --------------------------------------------------------------------------
# Report formatting
# --------------------------------------------------------------------------

def format_comparison_report(
    baseline_per_cat: dict[str, list[float]],
    candidate_per_cat: dict[str, list[float]],
    baseline_label: str,
    candidate_label: str,
    baseline_files: list[Path],
    candidate_files: list[Path],
    confidence: float = 0.95,
) -> str:
    lines: list[str] = []
    lines.append(f"# CIQS Comparison — {candidate_label} vs {baseline_label}")
    lines.append("")
    lines.append(f"> Baseline label: **{baseline_label}** ({len(baseline_files)} runs)")
    for p in baseline_files:
        lines.append(f"> - `{p}`")
    lines.append(f"> Candidate label: **{candidate_label}** ({len(candidate_files)} runs)")
    for p in candidate_files:
        lines.append(f"> - `{p}`")
    ci_pct = int(round(confidence * 100))
    lines.append(f"> Delta confidence level: {ci_pct}% (bootstrap, unpaired)")
    lines.append("")
    lines.append("## Per-category delta")
    lines.append("")
    lines.append(
        f"| Category | Baseline mean | Candidate mean | Δ | {ci_pct}% CI (Δ) | Verdict |"
    )
    lines.append(
        "|----------|---------------|-----------------|---|----------------|---------|"
    )

    categories = sorted(set(baseline_per_cat.keys()) | set(candidate_per_cat.keys()))
    verdicts: dict[str, str] = {}
    for cat in categories:
        b_vals = baseline_per_cat.get(cat, [])
        c_vals = candidate_per_cat.get(cat, [])
        if not b_vals or not c_vals:
            lines.append(
                f"| {cat} | {_fmt_mean(b_vals)} | {_fmt_mean(c_vals)} | — | — | insufficient-data |"
            )
            verdicts[cat] = "insufficient-data"
            continue
        b_mean = statistics.fmean(b_vals)
        c_mean = statistics.fmean(c_vals)
        delta = c_mean - b_mean
        low, high = bootstrap_delta_ci(b_vals, c_vals, confidence=confidence)
        verdict = classify_delta(low, high)
        verdicts[cat] = verdict
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"| {cat} | {b_mean:.1f} | {c_mean:.1f} | {sign}{delta:.1f} "
            f"| [{low:+.1f}, {high:+.1f}] | {verdict} |"
        )

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    imp = [c for c, v in verdicts.items() if v == "improved"]
    reg = [c for c, v in verdicts.items() if v == "regressed"]
    par = [c for c, v in verdicts.items() if v == "parity"]
    ins = [c for c, v in verdicts.items() if v == "insufficient-data"]
    lines.append(f"- **Improved:** {len(imp)} ({', '.join(imp) if imp else 'none'})")
    lines.append(f"- **Regressed:** {len(reg)} ({', '.join(reg) if reg else 'none'})")
    lines.append(f"- **Parity:** {len(par)} ({', '.join(par) if par else 'none'})")
    if ins:
        lines.append(
            f"- **Insufficient data:** {len(ins)} ({', '.join(ins)}) — at least one side had no scored records"
        )
    lines.append("")
    if reg:
        lines.append("**⚠ Attention:** regressions detected. Review before promoting.")
        lines.append("")
    elif imp and not reg:
        lines.append("**✓ Net improvement** with no regressions at the configured CI.")
        lines.append("")
    lines.append("> Verdicts reflect whether 0 falls inside the delta CI. A 'parity' verdict")
    lines.append("> does NOT mean 'no effect' — it means the effect size is smaller than the")
    lines.append("> sampling noise at the number of runs provided. Run more trials to narrow.")
    lines.append("")
    return "\n".join(lines)


def _fmt_mean(vals: list[float]) -> str:
    if not vals:
        return "—"
    return f"{statistics.fmean(vals):.1f}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CIQS two-mode comparison")
    parser.add_argument("--baseline-label", required=True,
                        help="Display label for the baseline set (e.g. 'vps-cpu')")
    parser.add_argument("--candidate-label", required=True,
                        help="Display label for the candidate set (e.g. 'vps-gpu')")
    parser.add_argument("--baseline", nargs="+", required=True,
                        help="Scored JSONL files for the baseline")
    parser.add_argument("--candidate", nargs="+", required=True,
                        help="Scored JSONL files for the candidate")
    parser.add_argument("--out", help="Output markdown path (default: stdout)")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--exit-nonzero-on-regression", action="store_true",
                        help="Exit with code 2 if any category regressed — for CI/automation")
    args = parser.parse_args(argv)

    summ = _get_summ()
    b_paths = [Path(p) for p in args.baseline]
    c_paths = [Path(p) for p in args.candidate]
    b_records = summ.load_scored(b_paths)
    c_records = summ.load_scored(c_paths)
    if not b_records:
        print("ERROR: no baseline records loaded", file=sys.stderr)
        return 2
    if not c_records:
        print("ERROR: no candidate records loaded", file=sys.stderr)
        return 2

    b_per_cat = summ.group_by_category(b_records)
    c_per_cat = summ.group_by_category(c_records)
    if not b_per_cat or not c_per_cat:
        print("ERROR: no scored records in one or both sets "
              "(did you run `ciqs_harness.py score` yet?)", file=sys.stderr)
        return 2

    report = format_comparison_report(
        b_per_cat, c_per_cat,
        args.baseline_label, args.candidate_label,
        b_paths, c_paths,
        confidence=args.confidence,
    )
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(report)

    if args.exit_nonzero_on_regression:
        # Lightweight re-scan of the just-computed verdicts.
        for cat in sorted(set(b_per_cat.keys()) & set(c_per_cat.keys())):
            b_vals = b_per_cat[cat]
            c_vals = c_per_cat[cat]
            if not b_vals or not c_vals:
                continue
            low, high = bootstrap_delta_ci(b_vals, c_vals, confidence=args.confidence)
            if classify_delta(low, high) == "regressed":
                print(f"REGRESSION in category {cat}: Δ CI=[{low:+.1f}, {high:+.1f}]",
                      file=sys.stderr)
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
