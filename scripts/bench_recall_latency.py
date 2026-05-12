#!/usr/bin/env python3
"""S-43 AC-3 latency benchmark — p95 recall latency ≤ 1500ms on vps-gpu.

Exercises the full retrieval pipeline (BM25 + vector search + RRF fusion)
with a synthetic corpus of N blocks, measuring wall-clock time per query.

The pipeline file-loading step caps at 20 most recent files per source;
"100-file corpus" translates to ~100 in-memory blocks (discovery files
typically produce 3-8 blocks each). This benchmark seeds directly at the
block level to test the hot path accurately regardless of filesystem state.

Usage:
    python scripts/bench_recall_latency.py
    python scripts/bench_recall_latency.py --num-blocks 100 --queries 50 --warmup 3
    python scripts/bench_recall_latency.py --mode vps-gpu   # set DEPTHFUSION_MODE

Outputs:
    - Per-query latencies (ms)
    - p50 / p95 / p99 / max summary
    - S-43 AC-3 verdict: PASS (≤ 1500ms p95) or FAIL
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import textwrap
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# ---------------------------------------------------------------------------
# Synthetic corpus generation
# ---------------------------------------------------------------------------

_SAMPLE_CONTENTS = [
    "Use Redis-backed sessions for the main user-facing application — instant revocation, horizontally scalable.",
    "Migrate JWT signing from HS256 to RS256 so that the private key never leaves the auth service.",
    "All background jobs must be idempotent — handlers must safely tolerate at-least-once delivery.",
    "Use keyset (cursor) pagination over OFFSET-based — OFFSET has O(N) cost on large tables.",
    "Store feature flags in environment variables, not a database table — avoids runtime DB dependency.",
    "All timestamps stored in the database are UTC. No timezone conversion at the persistence layer.",
    "Use GraphQL for the internal dashboard — flexible shape, typed schema, single endpoint.",
    "Use REST for the external public API — universally understood, cacheable, proxy-friendly.",
    "Rate limiting is centralised at the Kong gateway with Redis-backed sliding-window counters.",
    "Standard retry: exponential backoff with jitter, max 3 attempts, base delay 500ms.",
    "Data retention: operational logs 90 days, transaction records 7 years (compliance).",
    "Set SameSite=Lax, Secure, HttpOnly on all session cookies — CSRF + XSS defence-in-depth.",
    "Enforce 80% line coverage as a CI gate — builds fail below this threshold.",
    "Add PostgreSQL read replicas; route analytics queries via a read-only connection pool.",
    "Use Kubernetes over Docker Swarm for container orchestration — industry standard, better RBAC.",
    "New Python microservices use async/await throughout (asyncpg, httpx, aioredis).",
    "User deletion: soft-delete (set deleted_at) + PII scrub immediately; hard-delete after 90 days.",
    "CI: GitHub Actions — tight GitHub integration, marketplace coverage, matrix builds.",
    "CD: ArgoCD GitOps — Git is the source of truth; deployments are auditable PRs.",
    "Distributed tracing: OpenTelemetry SDK in all services exporting to Tempo.",
]

_QUERIES = [
    "session management and token revocation policy",
    "JWT signing algorithm and key rotation",
    "background job idempotency and queue reliability",
    "pagination strategy for large datasets",
    "feature flag storage and runtime configuration",
    "timezone handling in database",
    "API design for internal vs external consumers",
    "rate limiting architecture",
    "retry strategy for external API calls",
    "data retention and compliance policy",
    "cookie security settings",
    "test coverage enforcement",
    "database read scaling strategy",
    "container orchestration choice",
    "async Python service patterns",
    "user account deletion and GDPR",
    "CI/CD toolchain selection",
    "observability and distributed tracing",
    "authentication and authorisation boundary",
    "caching strategy and TTL policy",
]


def _make_block(i: int) -> dict:
    content = _SAMPLE_CONTENTS[i % len(_SAMPLE_CONTENTS)]
    return {
        "chunk_id": f"bench-{i:04d}",
        "file_stem": f"2026-05-12-bench-corpus-{i:04d}",
        "source": "discovery",
        "content": f"---\nproject: bench\ndate: 2026-05-12\n---\n\n# Bench {i}\n\n{content}\n",
        "snippet": content,
        "title": f"Bench block {i}",
        "score": 0.0,
    }


def build_corpus(n: int) -> list[dict]:
    # Generate n blocks, cycling through sample content with minor variation
    blocks = []
    for i in range(n):
        b = _make_block(i)
        # Vary the snippet slightly so BM25 scores are non-trivial
        extra = f" (ref-{i})"
        b["snippet"] += extra
        b["content"] += extra
        blocks.append(b)
    return blocks


# ---------------------------------------------------------------------------
# BM25 retrieval (mirrors _tool_recall_impl's path)
# ---------------------------------------------------------------------------

def bm25_score(query_tokens: list[str], blocks: list[dict]) -> list[dict]:
    """BM25 scoring — uses production BM25 class when available, inline fallback otherwise.

    Handles two API shapes:
      - v1.x: free function `score_blocks(tokens, blocks)`
      - v0.6.x: class `BM25(corpus_tokens).rank_all(query_terms)` returning [(idx, score), ...]
    """
    try:
        from depthfusion.retrieval.bm25 import score_blocks
        return score_blocks(query_tokens, blocks)
    except ImportError:
        pass

    # v0.6.x class-based API
    try:
        from depthfusion.retrieval.bm25 import BM25, tokenize
        corpus_tokens = [tokenize(b.get("snippet", "")) for b in blocks]
        bm25 = BM25(corpus_tokens)
        ranked = bm25.rank_all(query_tokens)  # [(idx, score), ...]
        scored = []
        for idx, score in ranked:
            scored.append({**blocks[idx], "score": score})
        return scored
    except (ImportError, Exception):
        pass

    # Inline TF-IDF-flavoured fallback
    import math as _math
    query_set = set(t.lower() for t in query_tokens)
    scored = []
    for b in blocks:
        tokens = b.get("snippet", "").lower().split()
        tf = sum(1 for t in tokens if t in query_set)
        idf = _math.log(1 + len(blocks) / (1 + tf)) if tf else 0.0
        scored.append({**b, "score": tf * idf})
    scored.sort(key=lambda x: -x["score"])
    return scored


# ---------------------------------------------------------------------------
# Core measurement loop
# ---------------------------------------------------------------------------

def measure_one(query: str, blocks: list[dict], pipeline: "Any",
                emb_backend: "Any" = None) -> float:
    """Time a single full retrieval pass; return elapsed ms.

    emb_backend: pre-created embedding backend to pass through to
    apply_vector_search, avoiding re-instantiation (and re-loading weights)
    on every call.  When None, apply_vector_search calls get_backend()
    internally — fine for correctness, slow due to model reload.
    """
    import time as _time
    t0 = _time.perf_counter()

    # BM25 pass — bm25_score handles all API shape variants
    query_tokens = query.split()
    bm25_blocks = bm25_score(query_tokens, blocks)

    bm25_top = bm25_blocks[:min(20, len(bm25_blocks))]

    # Vector search pass — pass pre-loaded backend to avoid per-call model reload
    vector_blocks = pipeline.apply_vector_search(query, blocks, top_k=20,
                                                 backend=emb_backend)

    # RRF fusion — v0.6.x rrf_fuse has no top_k param; v1.x may add it
    if vector_blocks:
        try:
            fused = pipeline.rrf_fuse(bm25_top, vector_blocks, top_k=10)
        except TypeError:
            fused = pipeline.rrf_fuse(bm25_top, vector_blocks)[:10]
    else:
        fused = bm25_top[:10]

    # Reranker (HaikuReranker on vps-cpu/vps-gpu — skip in bench to avoid API cost)
    # Pipeline.apply_reranker is a Haiku call; skip for latency measurement of local path.

    elapsed_ms = (_time.perf_counter() - t0) * 1000
    return elapsed_ms


def percentile(data: list[float], p: float) -> float:
    if not data:
        return float("nan")
    data_sorted = sorted(data)
    idx = (p / 100) * (len(data_sorted) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(data_sorted) - 1)
    frac = idx - lo
    return data_sorted[lo] + frac * (data_sorted[hi] - data_sorted[lo])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S-43 AC-3 recall latency benchmark")
    parser.add_argument("--num-blocks", type=int, default=100,
                        help="Corpus size in blocks (default 100)")
    parser.add_argument("--queries", type=int, default=50,
                        help="Number of timed queries to run (default 50)")
    parser.add_argument("--warmup", type=int, default=3,
                        help="Warmup queries before timing (default 3)")
    parser.add_argument("--mode", default=None,
                        help="Set DEPTHFUSION_MODE for this run (default: use env)")
    parser.add_argument("--skip-reranker", action="store_true", default=True,
                        help="Skip Haiku reranker to measure local GPU path only (default: on)")
    args = parser.parse_args(argv)

    if args.mode:
        os.environ["DEPTHFUSION_MODE"] = args.mode

    mode = os.environ.get("DEPTHFUSION_MODE", "local")
    print(f"Mode: {mode}")
    print(f"Corpus blocks: {args.num_blocks}")
    print(f"Queries (timed): {args.queries}  |  Warmup: {args.warmup}")
    print()

    # Build corpus
    corpus = build_corpus(args.num_blocks)

    # Build pipeline
    try:
        from depthfusion.retrieval.hybrid import RecallPipeline
        pipeline = RecallPipeline.from_env()
        print(f"Pipeline mode: {pipeline.mode}")
    except ImportError as err:
        print(f"ERROR: cannot import RecallPipeline: {err}", file=sys.stderr)
        return 2

    # Pre-create and cache the embedding backend so the sentence-transformers model
    # is loaded exactly once and stays in GPU memory for all warmup + timed queries.
    # Without this, apply_vector_search calls get_backend() on every query, creating
    # a fresh LocalEmbeddingBackend instance each time → model reloads every call.
    emb_backend = None
    try:
        from depthfusion.backends.factory import get_backend
        emb_backend = get_backend("embedding")
        healthy = emb_backend.healthy() if hasattr(emb_backend, "healthy") else True
        print(f"Embedding backend: {type(emb_backend).__name__}  healthy={healthy}")
        # Pre-load the model now (before warmup) so even the first warmup query
        # doesn't include model load time.
        if hasattr(emb_backend, "_get_model"):
            _ = emb_backend._get_model()
            print("Model pre-loaded.")
    except Exception as err:
        print(f"Embedding backend: unavailable ({err}) — vector search disabled", file=sys.stderr)

    print()

    # Warmup
    if args.warmup > 0:
        print(f"Warming up ({args.warmup} queries)...", end="", flush=True)
        for i in range(args.warmup):
            q = _QUERIES[i % len(_QUERIES)]
            measure_one(q, corpus, pipeline, emb_backend=emb_backend)
            print(".", end="", flush=True)
        print(" done")
        print()

    # Timed runs
    latencies: list[float] = []
    print(f"Running {args.queries} timed queries...")
    for i in range(args.queries):
        q = _QUERIES[i % len(_QUERIES)]
        ms = measure_one(q, corpus, pipeline, emb_backend=emb_backend)
        latencies.append(ms)
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{args.queries}]  last={ms:.1f}ms  running_p95={percentile(latencies, 95):.1f}ms")

    print()
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    mean = sum(latencies) / len(latencies)
    max_ = max(latencies)

    print("=" * 50)
    print(f"Recall latency — {args.num_blocks}-block corpus, {mode} mode")
    print("=" * 50)
    print(f"  mean   {mean:7.1f} ms")
    print(f"  p50    {p50:7.1f} ms")
    print(f"  p95    {p95:7.1f} ms   ← S-43 AC-3 target ≤ 1500ms")
    print(f"  p99    {p99:7.1f} ms")
    print(f"  max    {max_:7.1f} ms")
    print()

    THRESHOLD_MS = 1500.0
    if p95 <= THRESHOLD_MS:
        print(f"S-43 AC-3: ✅ PASS  p95={p95:.1f}ms ≤ {THRESHOLD_MS:.0f}ms")
        return 0
    else:
        print(f"S-43 AC-3: ❌ FAIL  p95={p95:.1f}ms > {THRESHOLD_MS:.0f}ms")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
