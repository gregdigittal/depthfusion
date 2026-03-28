"""Reciprocal Rank Fusion (RRF) for DepthFusion.

Standard RRF formula: score(d) = Σ 1/(k + rank_i(d)), k=60 by default.
Reference: Cormack, Clarke & Buettcher (2009).
"""
from __future__ import annotations

from collections import defaultdict


def rrf_score(ranks: list[int], k: int = 60) -> float:
    """Compute RRF score for a document given its ranks across multiple retrievers.

    Args:
        ranks: 1-based rank positions of the document in each retriever's list.
        k:     Constant preventing high impact of top-ranked documents (default 60).

    Returns:
        Summed reciprocal ranks. 0.0 for an empty ranks list.
    """
    return sum(1.0 / (k + r) for r in ranks)


def fuse(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked lists of doc IDs using RRF.

    Args:
        ranked_lists: Each inner list is an ordered sequence of document IDs
                      (position 0 = rank 1, position 1 = rank 2, …).
        k:            RRF constant (default 60).

    Returns:
        Sorted list of (doc_id, score) tuples, descending by score.
        Ties are broken alphabetically by doc_id for determinism.
    """
    scores: dict[str, float] = defaultdict(float)

    for ranked_list in ranked_lists:
        for rank_0_based, doc_id in enumerate(ranked_list):
            rank = rank_0_based + 1  # convert to 1-based
            scores[doc_id] += 1.0 / (k + rank)

    # Sort descending by score, then ascending by doc_id for stable tie-breaking
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
