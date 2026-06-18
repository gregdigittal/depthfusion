"""Performance regression tests — E-61.

Verifies that the BM25 search path meets the p95 < 500ms SLO on a
10k-record synthetic dataset.

Methodology
-----------
- Build a BM25 corpus from 10 000 synthetic blocks.
- Run 100 searches across diverse query terms.
- Assert that the 95th-percentile wall-clock time is under 500 ms.
- Also assert the median is under 100 ms (catches gross regressions early).

The 500 ms SLO is deliberately generous vs the S-197 AC-2 target of 300 ms
server-side — this test exercises Python-layer scoring without network/DB
overhead, so the tighter production target is validated separately by the
load harness.
"""
from __future__ import annotations

import random
import string
import time
from typing import List

import pytest

from depthfusion.retrieval.bm25 import BM25, tokenize

# ---------------------------------------------------------------------------
# Corpus builder
# ---------------------------------------------------------------------------

_WORD_POOL = [
    "memory", "context", "recall", "fusion", "project", "session",
    "agent", "pipeline", "query", "index", "vector", "score",
    "depth", "cognitive", "discovery", "knowledge", "token", "chunk",
    "embed", "latency", "cache", "tier", "block", "retrieval",
    "classification", "export", "policy", "authz", "identity", "principal",
    "workflow", "orchestrator", "dispatch", "signal", "entropy",
    "semantic", "lexical", "hybrid", "rerank", "bm25",
    "architecture", "service", "endpoint", "router", "backend",
    "ingest", "transform", "parse", "extract", "compress",
]

_QUERY_POOL = [
    "memory recall pipeline",
    "cognitive scoring context",
    "export policy classification",
    "session compression agent",
    "vector index hybrid retrieval",
    "query latency cache tier",
    "project discovery knowledge",
    "bm25 rerank score",
    "semantic lexical fusion",
    "authz principal identity",
    "dispatch orchestrator workflow",
    "token chunk embed",
    "backend ingest transform",
    "architecture service endpoint",
    "signal entropy compress",
]

_RNG = random.Random(42)  # deterministic corpus


def _make_block(idx: int, word_count: int = 80) -> str:
    """Generate a synthetic block with real-ish vocabulary."""
    words = [_RNG.choice(_WORD_POOL) for _ in range(word_count)]
    # Sprinkle a few unique identifiers to make blocks distinguishable
    words[0] = f"block{idx}"
    words[1] = f"id{idx % 500}"
    return " ".join(words)


def _build_corpus(n: int = 10_000) -> list[list[str]]:
    """Return tokenised corpus of n synthetic blocks."""
    return [tokenize(_make_block(i)) for i in range(n)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bm25_10k() -> BM25:
    """BM25 index over 10k synthetic records — built once per module."""
    corpus = _build_corpus(10_000)
    return BM25(corpus)


@pytest.fixture(scope="module")
def query_terms_list() -> list[list[str]]:
    """100 tokenised queries drawn from the query pool."""
    rng = random.Random(99)
    queries = []
    for _ in range(100):
        raw = rng.choice(_QUERY_POOL)
        queries.append(tokenize(raw))
    return queries


# ---------------------------------------------------------------------------
# E-61: p95 latency test
# ---------------------------------------------------------------------------

class TestSearchP95Latency:
    """Assert search p95 < 500ms on 10k record dataset (E-61 performance gate)."""

    def test_p95_under_500ms(
        self,
        bm25_10k: BM25,
        query_terms_list: list[list[str]],
    ) -> None:
        """Run 100 BM25 searches and assert p95 wall-clock time < 500ms."""
        latencies: list[float] = []
        for terms in query_terms_list:
            t0 = time.perf_counter()
            results = bm25_10k.rank_all(terms)
            t1 = time.perf_counter()
            assert len(results) == 10_000, "rank_all must return scores for every document"
            latencies.append((t1 - t0) * 1000)  # convert to ms

        latencies.sort()
        p95_ms = latencies[int(0.95 * len(latencies))]
        p50_ms = latencies[int(0.50 * len(latencies))]
        max_ms = latencies[-1]

        # Diagnostic output (visible with pytest -s)
        print(
            f"\nBM25 search latency (10k records, 100 queries): "
            f"p50={p50_ms:.1f}ms  p95={p95_ms:.1f}ms  max={max_ms:.1f}ms"
        )

        assert p95_ms < 500.0, (
            f"Search p95 latency {p95_ms:.1f}ms exceeds 500ms SLO. "
            "Check for algorithmic regressions in BM25.rank_all()."
        )

    def test_median_under_100ms(
        self,
        bm25_10k: BM25,
        query_terms_list: list[list[str]],
    ) -> None:
        """Median should be well under 100ms — catches gross regressions early."""
        latencies: list[float] = []
        for terms in query_terms_list:
            t0 = time.perf_counter()
            bm25_10k.rank_all(terms)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)

        latencies.sort()
        p50_ms = latencies[50]

        assert p50_ms < 100.0, (
            f"Median search latency {p50_ms:.1f}ms exceeds 100ms. "
            "This is an early-warning indicator of BM25 regression."
        )


# ---------------------------------------------------------------------------
# E-61: Index build time (regression guard)
# ---------------------------------------------------------------------------

class TestCorpusBuildTime:
    """Index construction for 10k records should complete in < 2s."""

    def test_index_build_under_2s(self) -> None:
        t0 = time.perf_counter()
        corpus = _build_corpus(10_000)
        BM25(corpus)
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        assert elapsed_ms < 2000.0, (
            f"BM25 index build over 10k docs took {elapsed_ms:.0f}ms (> 2000ms). "
            "Regression in tokenise() or BM25.__init__()."
        )


# ---------------------------------------------------------------------------
# E-61: Correctness baseline (not latency — ensures the corpus is exercised)
# ---------------------------------------------------------------------------

class TestSearchCorrectness:
    """Quick sanity checks that rank_all returns sensible results."""

    def test_known_term_ranks_matching_docs_first(self, bm25_10k: BM25) -> None:
        """'block0' only appears in doc 0 — it should rank first."""
        results = bm25_10k.rank_all(["block0"])
        assert results[0][0] == 0, "block0 must rank first when searching for 'block0'"

    def test_empty_query_returns_zero_scores(self, bm25_10k: BM25) -> None:
        """An empty query should return all docs with score 0.0."""
        results = bm25_10k.rank_all([])
        scores = [s for _, s in results]
        assert all(s == 0.0 for s in scores), "Empty query must produce zero scores"

    def test_rank_all_returns_all_docs(self, bm25_10k: BM25) -> None:
        results = bm25_10k.rank_all(["memory"])
        assert len(results) == 10_000
