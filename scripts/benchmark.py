#!/usr/bin/env python3
"""Benchmark harness for DepthFusion recall performance.

Runs BM25 retrieval against a goldset fixture and produces machine-readable
JSON metrics. Designed to work in local mode only — no API keys required.

Usage:
    python scripts/benchmark.py [--goldset PATH] [--top-k INT] [--output PATH]
                                 [--mode local] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make the src tree importable when run from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from depthfusion.retrieval.bm25 import BM25 as _BM25
from depthfusion.retrieval.bm25 import tokenize as _tokenize_bm25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_hash() -> str:
    """Return the short HEAD commit hash, or 'unknown' on error."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _percentile(values: list[float], pct: float) -> float:
    """Return the *pct* percentile of *values* (0-100 scale)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (pct / 100) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return sorted_vals[-1]
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _run_bm25_for_entry(
    entry: dict,
    top_k: int,
) -> tuple[list[str], float]:
    """Run BM25 over the corpus in *entry* and return (ranked_chunk_ids, latency_ms).

    Only the documents listed in entry["corpus"] are indexed — no user files.
    """
    corpus = entry["corpus"]
    query = entry["query"]

    t_start = time.perf_counter()

    # Build BM25 index from this entry's corpus only.
    corpus_tokens = [_tokenize_bm25(doc["content"]) for doc in corpus]
    query_tokens = _tokenize_bm25(query)
    bm25 = _BM25(corpus_tokens)

    ranked = bm25.rank_all(query_tokens)  # list of (doc_idx, score) desc

    # Collect top-k chunk IDs.
    top_ids: list[str] = []
    for doc_idx, _score in ranked[:top_k]:
        top_ids.append(corpus[doc_idx]["chunk_id"])

    t_end = time.perf_counter()
    latency_ms = (t_end - t_start) * 1000.0

    return top_ids, latency_ms


# ---------------------------------------------------------------------------
# Core benchmark loop
# ---------------------------------------------------------------------------

def run_benchmark(
    goldset_path: Path,
    top_k: int = 5,
    mode: str = "local",
    quiet: bool = False,
) -> dict:
    """Run the full benchmark and return the result dict."""
    if not goldset_path.exists():
        raise FileNotFoundError(f"Goldset not found: {goldset_path}")

    entries: list[dict] = []
    with goldset_path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {lineno}: {exc}") from exc

    if not entries:
        raise ValueError(f"Goldset is empty: {goldset_path}")

    if not quiet:
        print(f"Loaded {len(entries)} queries from {goldset_path}", file=sys.stderr)

    per_query: list[dict] = []
    latencies: list[float] = []
    hit_at_1 = 0
    hit_at_k = 0

    for i, entry in enumerate(entries, 1):
        query = entry["query"]
        relevant = set(entry["relevant_chunk_ids"])

        if not quiet:
            print(f"  [{i}/{len(entries)}] {query[:60]!r}…", file=sys.stderr)

        top_ids, latency_ms = _run_bm25_for_entry(entry, top_k)
        latencies.append(latency_ms)

        retrieved_set = set(top_ids)
        top_1_hit = bool(top_ids) and top_ids[0] in relevant
        top_k_hit = bool(retrieved_set & relevant)

        if top_1_hit:
            hit_at_1 += 1
        if top_k_hit:
            hit_at_k += 1

        per_query.append({
            "query": query,
            "description": entry.get("description", ""),
            "relevant_chunk_ids": list(relevant),
            "retrieved_chunk_ids": top_ids,
            "top_1_hit": top_1_hit,
            "top_k_hit": top_k_hit,
            "latency_ms": round(latency_ms, 3),
        })

    q_count = len(entries)
    precision_at_1 = hit_at_1 / q_count
    precision_at_k = hit_at_k / q_count
    fallback_rate = 1.0 - precision_at_k
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_hash": _git_hash(),
        "mode": mode,
        "top_k": top_k,
        "goldset_path": str(goldset_path),
        "query_count": q_count,
        "metrics": {
            "p50_latency_ms": {"value": round(p50, 3), "basis": "measured"},
            "p95_latency_ms": {"value": round(p95, 3), "basis": "measured"},
            "precision_at_1": {"value": round(precision_at_1, 4), "basis": "measured"},
            f"precision_at_{top_k}": {"value": round(precision_at_k, 4), "basis": "measured"},
            f"hit_rate_at_{top_k}": {"value": round(precision_at_k, 4), "basis": "measured"},
            # precision_at_5 and hit_rate_at_5 as canonical aliases
            "precision_at_5": {"value": round(precision_at_k, 4), "basis": "measured"},
            "hit_rate_at_5": {"value": round(precision_at_k, 4), "basis": "measured"},
            "fallback_rate": {"value": round(fallback_rate, 4), "basis": "measured"},
            "cost_estimate_usd": {"value": 0.0, "basis": "estimated"},
        },
        "per_query": per_query,
    }

    if not quiet:
        print(
            f"\nResults: precision@1={precision_at_1:.3f}  "
            f"precision@{top_k}={precision_at_k:.3f}  "
            f"p50={p50:.1f}ms  p95={p95:.1f}ms",
            file=sys.stderr,
        )

    return result


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DepthFusion recall benchmark (local BM25 mode).",
    )
    parser.add_argument(
        "--goldset",
        default=str(REPO_ROOT / "tests" / "fixtures" / "recall_goldset.jsonl"),
        help="Path to the JSONL goldset fixture.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        dest="top_k",
        help="Number of results to retrieve per query.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write JSON output to this path (default: stdout).",
    )
    parser.add_argument(
        "--mode",
        default="local",
        help="Retrieval mode label (informational; only 'local' BM25 is implemented).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output on stderr.",
    )
    args = parser.parse_args()

    result = run_benchmark(
        goldset_path=Path(args.goldset),
        top_k=args.top_k,
        mode=args.mode,
        quiet=args.quiet,
    )

    output_json = json.dumps(result, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json)
        if not args.quiet:
            print(f"Output written to {out_path}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
