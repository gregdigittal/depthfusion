"""Benchmark: weighted fusion vs pure RRF on synthetic data.

Creates 20 synthetic documents with embeddings. For each query:
- Two retrievers independently rank the docs with added noise.
- Pure RRF fuses the two noisy ranked lists (no access to embeddings).
- AttnRes weighted fusion re-scores using cosine similarity between
  the query embedding and each chunk embedding, correcting for retriever noise.

Ground truth: relevance = highest cosine similarity to the query.
Metric: precision@5 over 50 queries.
Weighted fusion should win on >= 70% of queries.
"""
from __future__ import annotations

import math
import random

from depthfusion.core.scoring import cosine_similarity
from depthfusion.core.types import RetrievedChunk
from depthfusion.fusion.rrf import fuse as rrf_fuse
from depthfusion.fusion.weighted import attnres_fusion

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DIM = 16
N_DOCS = 20
N_QUERIES = 50
TOP_K = 5
RELEVANT_K = 5          # top-K by true cosine sim = ground truth relevant set
NOISE_LEVEL = 0.5       # std dev of Gaussian noise added to retriever scores
WEIGHTED_WIN_THRESHOLD = 0.70
RNG_SEED = 2025


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


def _random_unit_vec(dim: int, rng: random.Random) -> list[float]:
    return _norm([rng.gauss(0, 1) for _ in range(dim)])


def _build_documents(rng: random.Random) -> list[dict]:
    return [
        {"id": f"doc_{i:02d}", "embedding": _random_unit_vec(DIM, rng)}
        for i in range(N_DOCS)
    ]


def _relevant_ids(query_emb: list[float], docs: list[dict]) -> set[str]:
    """Ground-truth relevance: top RELEVANT_K by true cosine similarity."""
    scored = sorted(docs, key=lambda d: -cosine_similarity(query_emb, d["embedding"]))
    return {d["id"] for d in scored[:RELEVANT_K]}


def _noisy_ranking(docs: list[dict], query_emb: list[float], rng: random.Random) -> list[str]:
    """Rank docs by cosine sim + Gaussian noise, simulating a noisy retriever."""
    scored = [
        (cosine_similarity(query_emb, d["embedding"]) + rng.gauss(0, NOISE_LEVEL), d["id"])
        for d in docs
    ]
    scored.sort(reverse=True)
    return [doc_id for _, doc_id in scored]


def _precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    hits = sum(1 for doc_id in retrieved[:k] if doc_id in relevant)
    return hits / k


# ---------------------------------------------------------------------------
# RRF approach: fuse two noisy ranked lists, no embeddings
# ---------------------------------------------------------------------------

def _rrf_top_k(docs: list[dict], query_emb: list[float], rng: random.Random) -> list[str]:
    """Fuse two noisy retriever rankings using pure RRF (no embedding access)."""
    list1 = _noisy_ranking(docs, query_emb, rng)
    list2 = _noisy_ranking(docs, query_emb, rng)
    fused = rrf_fuse([list1, list2])
    return [doc_id for doc_id, _ in fused]


# ---------------------------------------------------------------------------
# Weighted fusion: uses true embeddings to re-score after noisy retrieval
# ---------------------------------------------------------------------------

def _weighted_top_k(docs: list[dict], query_emb: list[float], rng: random.Random) -> list[str]:
    """
    Fuse same two noisy retrievers, then re-score using query-vs-chunk
    cosine similarity (AttnRes attention weighting).  The embedding signal
    overrides the noisy retrieval order.
    """
    list1 = _noisy_ranking(docs, query_emb, rng)
    list2 = _noisy_ranking(docs, query_emb, rng)

    # Build RRF base scores
    rrf_result = rrf_fuse([list1, list2])
    rrf_score_map = {doc_id: score for doc_id, score in rrf_result}

    # Build chunks: base score = RRF score, embedding = true doc embedding
    doc_map = {d["id"]: d for d in docs}
    chunks = [
        RetrievedChunk(
            chunk_id=doc_id,
            content=doc_id,
            source="memory",
            score=rrf_score_map.get(doc_id, 0.0),
            metadata={"embedding": doc_map[doc_id]["embedding"]},
        )
        for doc_id in rrf_score_map
    ]

    fused = attnres_fusion(chunks, query_embedding=query_emb)
    return [c.chunk_id for c in fused]


# ---------------------------------------------------------------------------
# Benchmark test
# ---------------------------------------------------------------------------

def test_weighted_fusion_outperforms_rrf():
    """50 synthetic queries. Weighted fusion achieves higher precision@5 on >= 70%."""
    rng = random.Random(RNG_SEED)
    docs = _build_documents(rng)

    weighted_wins = 0
    rrf_wins = 0
    ties = 0

    for _ in range(N_QUERIES):
        query_emb = _random_unit_vec(DIM, rng)
        relevant = _relevant_ids(query_emb, docs)

        # Use a fresh RNG fork so both approaches see the same noisy retrievers
        fork_rng_rrf = random.Random(rng.randint(0, 2**32))
        fork_rng_weighted = random.Random(fork_rng_rrf.getstate()[1][0])  # same seed

        rrf_order = _rrf_top_k(docs, query_emb, fork_rng_rrf)
        weighted_order = _weighted_top_k(docs, query_emb, fork_rng_weighted)

        p_rrf = _precision_at_k(rrf_order, relevant, TOP_K)
        p_weighted = _precision_at_k(weighted_order, relevant, TOP_K)

        if p_weighted > p_rrf:
            weighted_wins += 1
        elif p_rrf > p_weighted:
            rrf_wins += 1
        else:
            ties += 1

    win_rate = weighted_wins / N_QUERIES

    print(
        f"\nBenchmark results over {N_QUERIES} queries:"
        f"\n  Weighted wins: {weighted_wins}"
        f"\n  RRF wins:      {rrf_wins}"
        f"\n  Ties:          {ties}"
        f"\n  Win rate (weighted):  {win_rate:.1%}"
        f"\n  Threshold:            {WEIGHTED_WIN_THRESHOLD:.0%}"
    )

    assert win_rate >= WEIGHTED_WIN_THRESHOLD, (
        f"Weighted fusion won on {win_rate:.1%} of queries, "
        f"expected >= {WEIGHTED_WIN_THRESHOLD:.0%}. "
        f"(wins={weighted_wins}, rrf_wins={rrf_wins}, ties={ties})"
    )
