#!/usr/bin/env python3
"""Standalone BM25 benchmark — outputs JSON for regression gating.

Mirrors the methodology in tests/test_performance.py but writes structured
results to --output rather than asserting inline. The CI workflow calls this
script then passes the output to check_benchmark_regression.py.

Usage
-----
  python scripts/run_benchmark.py --output benchmark-results.json
  python scripts/run_benchmark.py --output benchmark-results.json --n-docs 10000 --n-queries 100
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

# Allow running without installing the package (editable installs not always available in CI)
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))

from depthfusion.retrieval.bm25 import BM25, tokenize  # noqa: E402

_WORD_POOL = [
    "memory", "context", "recall", "fusion", "project", "session",
    "agent", "pipeline", "query", "index", "vector", "score",
    "depth", "cognitive", "discovery", "knowledge", "token", "chunk",
    "embed", "latency", "cache", "tier", "block", "retrieval",
    "classification", "export", "policy", "authz", "identity", "principal",
    "workflow", "orchestrator", "dispatch", "signal", "entropy",
    "semantic", "lexical", "hybrid", "rerank", "bm25",
]

_QUERY_POOL = [
    "memory recall pipeline",
    "cognitive scoring context",
    "export policy classification",
    "session compression agent",
    "vector index hybrid retrieval",
    "query latency cache tier",
    "project discovery knowledge",
    "authz identity principal",
    "bm25 rerank score",
    "semantic lexical fusion",
]


def _make_block(idx: int, rng: random.Random) -> dict:
    words = rng.choices(_WORD_POOL, k=rng.randint(10, 50))
    return {"id": f"b{idx}", "content": " ".join(words), "score": 0.0}


def run(n_docs: int = 10_000, n_queries: int = 100, seed: int = 42) -> dict:
    rng = random.Random(seed)

    # --- index build ---
    corpus = [_make_block(i, rng) for i in range(n_docs)]
    t_build_start = time.perf_counter()
    bm25 = BM25(corpus)
    build_ms = (time.perf_counter() - t_build_start) * 1000

    # --- search latency ---
    timings_ms: list[float] = []
    for i in range(n_queries):
        query = _QUERY_POOL[i % len(_QUERY_POOL)]
        terms = tokenize(query)
        t0 = time.perf_counter()
        bm25.rank_all(terms)
        timings_ms.append((time.perf_counter() - t0) * 1000)

    sorted_timings = sorted(timings_ms)
    p95_idx = max(0, int(len(sorted_timings) * 0.95) - 1)

    return {
        "n_docs": n_docs,
        "n_queries": n_queries,
        "seed": seed,
        "build_ms": round(build_ms, 3),
        "p50_ms": round(statistics.median(timings_ms), 3),
        "p95_ms": round(sorted_timings[p95_idx], 3),
        "min_ms": round(sorted_timings[0], 3),
        "max_ms": round(sorted_timings[-1], 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run BM25 benchmark and write results to JSON."
    )
    parser.add_argument("--output", type=Path, required=True, help="Path to write results JSON")
    parser.add_argument("--n-docs", type=int, default=10_000, help="Corpus size (default: 10000)")
    parser.add_argument("--n-queries", type=int, default=100, help="Number of queries (default: 100)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default: 42)")
    args = parser.parse_args()

    print(f"Running benchmark: n_docs={args.n_docs}, n_queries={args.n_queries}", flush=True)
    results = run(n_docs=args.n_docs, n_queries=args.n_queries, seed=args.seed)

    args.output.write_text(json.dumps(results, indent=2))
    print(
        f"Results: build={results['build_ms']:.1f}ms  "
        f"p50={results['p50_ms']:.2f}ms  "
        f"p95={results['p95_ms']:.2f}ms  "
        f"→ {args.output}"
    )


if __name__ == "__main__":
    main()
