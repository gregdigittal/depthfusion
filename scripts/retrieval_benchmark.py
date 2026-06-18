#!/usr/bin/env python3
"""ACL-aware retrieval benchmark — T-574.

Runs 100 searches with and without ACL trimming via the post-rank verifier,
records p50/p95/p99 latencies, and writes a summary to
``docs/benchmarks/acl-retrieval-YYYYMMDD.md``.

The benchmark builds a synthetic in-memory corpus so it runs without any
external dependencies (no real DB, no API keys, no network).

Usage::

    python scripts/retrieval_benchmark.py [--runs N] [--output PATH] [--quiet]

Output
------
- Markdown report written to ``docs/benchmarks/acl-retrieval-<date>.md``
- Summary printed to stdout (unless ``--quiet``)

Exit code 0 on success; 1 on unrecoverable error.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make src importable when run directly from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# ---------------------------------------------------------------------------
# Imports from depthfusion — imported after sys.path fixup
# ---------------------------------------------------------------------------

from depthfusion.retrieval.acl_verifier import verify_acl  # noqa: E402
from depthfusion.retrieval.bm25 import BM25, tokenize  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal principal stub (avoids importing the full identity / OIDC stack)
# ---------------------------------------------------------------------------


@dataclass
class _StubPrincipal:
    """Minimal principal compatible with verify_acl's duck-typed interface."""

    principal_id: str
    groups: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Corpus generation
# ---------------------------------------------------------------------------

_TOPICS = [
    "authentication token security login session",
    "database schema migration postgres indexing",
    "frontend react component rendering hooks",
    "CI pipeline artifact build deploy",
    "encryption key rotation certificate TLS",
    "logging observability metrics tracing spans",
    "feature flag rollout canary experiment",
    "cache invalidation Redis TTL eviction",
    "billing subscription webhook payment stripe",
    "rate limiting throttle backpressure queues",
]


def _build_corpus(size: int = 200) -> list[dict[str, Any]]:
    """Generate a synthetic retrieval corpus.

    Each document has:
    - ``chunk_id``   — unique ID
    - ``content``    — synthetic text drawn from topic pool
    - ``score``      — arbitrary BM25-style score placeholder
    - ``acl_allow``  — owner principal (alternating between "alice" and "bob")
    """
    corpus = []
    for i in range(size):
        topic = _TOPICS[i % len(_TOPICS)]
        owner = "alice" if i % 2 == 0 else "bob"
        corpus.append(
            {
                "chunk_id": str(uuid.uuid4()),
                "content": f"{topic} variant-{i}",
                "snippet": f"{topic} variant-{i}",
                "score": 1.0 - (i % 10) * 0.05,
                "acl_allow": [owner],
            }
        )
    return corpus


# ---------------------------------------------------------------------------
# Query pool
# ---------------------------------------------------------------------------

_QUERIES = [
    "authentication token",
    "database schema",
    "react component",
    "deploy pipeline",
    "encryption certificate",
    "metrics tracing",
    "feature flag canary",
    "cache eviction",
    "payment stripe",
    "rate limiting",
    "security login session",
    "postgres indexing migration",
    "rendering hooks frontend",
    "artifact build CI",
    "key rotation TLS",
    "logging observability",
    "rollout experiment",
    "Redis TTL",
    "subscription webhook",
    "throttle backpressure",
]


# ---------------------------------------------------------------------------
# BM25 search (simplified — tokenise query then return top-k scored blocks)
# ---------------------------------------------------------------------------


def _bm25_search(
    query: str,
    corpus: list[dict[str, Any]],
    bm25: BM25,
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """Run BM25 over the corpus and return the top-k results.

    Returns dicts from the corpus with a ``score`` key set to the BM25 score.
    Zero-score results are excluded.
    """
    query_terms = tokenize(query)
    ranked = bm25.rank_all(query_terms)
    results = []
    for idx, score in ranked[:top_k]:
        if score <= 0.0:
            break
        doc = dict(corpus[idx])
        doc["score"] = score
        results.append(doc)
    return results


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float:
    """Return the *pct*-th percentile (0–100) of *values*."""
    if not values:
        return 0.0
    sv = sorted(values)
    idx = (pct / 100.0) * (len(sv) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sv):
        return sv[-1]
    frac = idx - lo
    return sv[lo] + frac * (sv[hi] - sv[lo])


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_benchmark(runs: int = 100) -> dict[str, Any]:
    """Run the benchmark and return a results dict.

    Parameters
    ----------
    runs:
        Number of search iterations per condition (with ACL / without ACL).

    Returns
    -------
    dict with keys:
        - ``without_acl``: {p50, p95, p99, mean} latencies in ms
        - ``with_acl``:    {p50, p95, p99, mean} latencies in ms
        - ``corpus_size``:  number of documents in the corpus
        - ``runs``:         number of iterations per condition
        - ``without_acl_result_counts``:  list of result counts per run
        - ``with_acl_result_counts``:     list of result counts per run (alice only)
    """
    corpus = _build_corpus(size=200)

    # Build BM25 index once — reused across all runs.
    tokenized_docs = [tokenize(doc["content"]) for doc in corpus]
    bm25 = BM25(tokenized_docs)

    alice = _StubPrincipal(principal_id="alice", groups=[])

    without_acl_latencies: list[float] = []
    with_acl_latencies: list[float] = []
    without_acl_counts: list[int] = []
    with_acl_counts: list[int] = []

    query_pool = _QUERIES * (runs // len(_QUERIES) + 1)

    for i in range(runs):
        query = query_pool[i % len(query_pool)]

        # --- Without ACL trimming ---
        t0 = time.perf_counter()
        raw_results = _bm25_search(query, corpus, bm25, top_k=20)
        t1 = time.perf_counter()
        without_acl_latencies.append((t1 - t0) * 1000.0)
        without_acl_counts.append(len(raw_results))

        # --- With ACL trimming ---
        t2 = time.perf_counter()
        raw_results2 = _bm25_search(query, corpus, bm25, top_k=20)
        acl_results = verify_acl(raw_results2, principal=alice)
        t3 = time.perf_counter()
        with_acl_latencies.append((t3 - t2) * 1000.0)
        with_acl_counts.append(len(acl_results))

    def _stats(latencies: list[float]) -> dict[str, float]:
        return {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "mean": sum(latencies) / len(latencies) if latencies else 0.0,
        }

    return {
        "runs": runs,
        "corpus_size": len(corpus),
        "without_acl": _stats(without_acl_latencies),
        "with_acl": _stats(with_acl_latencies),
        "without_acl_result_counts": without_acl_counts,
        "with_acl_result_counts": with_acl_counts,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _fmt_ms(v: float) -> str:
    return f"{v:.3f} ms"


def build_report(results: dict[str, Any], *, git_hash: str) -> str:
    """Render a Markdown benchmark report from results dict."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    runs = results["runs"]
    corpus_size = results["corpus_size"]
    wo = results["without_acl"]
    wa = results["with_acl"]

    # Average result counts
    wo_counts = results["without_acl_result_counts"]
    wa_counts = results["with_acl_result_counts"]
    wo_avg_results = sum(wo_counts) / len(wo_counts) if wo_counts else 0.0
    wa_avg_results = sum(wa_counts) / len(wa_counts) if wa_counts else 0.0

    # Overhead
    p50_overhead = wa["p50"] - wo["p50"]
    p95_overhead = wa["p95"] - wo["p95"]
    p99_overhead = wa["p99"] - wo["p99"]

    lines = [
        f"# ACL Retrieval Benchmark — {now[:10]}",
        "",
        f"**Generated:** {now} UTC  ",
        f"**Git commit:** `{git_hash}`  ",
        f"**Runs per condition:** {runs}  ",
        f"**Corpus size:** {corpus_size} documents  ",
        "",
        "## Summary",
        "",
        "| Condition | p50 | p95 | p99 | mean | avg results |",
        "|-----------|-----|-----|-----|------|-------------|",
        f"| Without ACL trimming | {_fmt_ms(wo['p50'])} | {_fmt_ms(wo['p95'])} | {_fmt_ms(wo['p99'])} | {_fmt_ms(wo['mean'])} | {wo_avg_results:.1f} |",
        f"| With ACL trimming    | {_fmt_ms(wa['p50'])} | {_fmt_ms(wa['p95'])} | {_fmt_ms(wa['p99'])} | {_fmt_ms(wa['mean'])} | {wa_avg_results:.1f} |",
        "",
        "## Overhead (ACL trimming vs raw retrieval)",
        "",
        "| Metric | Absolute overhead |",
        "|--------|-------------------|",
        f"| p50    | {_fmt_ms(p50_overhead)} |",
        f"| p95    | {_fmt_ms(p95_overhead)} |",
        f"| p99    | {_fmt_ms(p99_overhead)} |",
        "",
        "## Notes",
        "",
        "- **Without ACL trimming**: BM25 search only (top-20 candidates).",
        "- **With ACL trimming**: BM25 search + `verify_acl()` post-rank pass.",
        "- Principal `alice` owns 50% of corpus documents (alternating ownership).",
        "- Corpus is synthetic (in-memory); no I/O latency is included.",
        "- All timings are wall-clock via `time.perf_counter()`.",
        "",
        "## Methodology",
        "",
        "The benchmark builds a 200-document synthetic corpus where half the",
        "documents belong to principal `alice` and half to `bob`.  Each run",
        "executes a BM25 search followed (in the ACL condition) by the",
        "`verify_acl()` post-rank verification pass from",
        "`depthfusion.retrieval.acl_verifier`.  The two conditions are measured",
        "sequentially within the same process to minimise JIT-warmup skew.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACL-aware retrieval benchmark (T-574).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=100,
        help="Number of search iterations per condition (default: 100).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Path for the output Markdown report.  "
            "Defaults to docs/benchmarks/acl-retrieval-YYYYMMDD.md."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout output.",
    )
    args = parser.parse_args(argv)

    if not args.quiet:
        print(f"Running ACL retrieval benchmark ({args.runs} runs per condition)…")

    results = run_benchmark(runs=args.runs)
    git_hash = _git_hash()
    report = build_report(results, git_hash=git_hash)

    # Determine output path.
    if args.output:
        out_path = Path(args.output)
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        out_path = REPO_ROOT / "docs" / "benchmarks" / f"acl-retrieval-{date_str}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    if not args.quiet:
        wo = results["without_acl"]
        wa = results["with_acl"]
        print(f"\nResults ({args.runs} runs each, corpus={results['corpus_size']} docs):")
        print(f"  Without ACL: p50={wo['p50']:.3f}ms  p95={wo['p95']:.3f}ms  p99={wo['p99']:.3f}ms")
        print(f"  With ACL:    p50={wa['p50']:.3f}ms  p95={wa['p95']:.3f}ms  p99={wa['p99']:.3f}ms")
        print(f"\nReport written to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
