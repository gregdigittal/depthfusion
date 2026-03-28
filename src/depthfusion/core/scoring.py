"""Core scoring functions for DepthFusion.

Implements AttnRes-inspired depth-wise attention analogs for block-level
retrieval weighting. Reference: arXiv:2603.15031 Section 3.2.

All functions operate on plain Python lists to avoid NumPy as a hard
dependency in the critical import path, while remaining NumPy-compatible.
"""
from __future__ import annotations

import math


def softmax_scores(scores: list[float]) -> list[float]:
    """Compute softmax over a list of raw scores.

    Numerically stable via the max-subtraction trick (prevents overflow
    for large score values, e.g. cosine similarities scaled by 1000).

    Args:
        scores: Raw logit-like scores for each item.

    Returns:
        Probability distribution (sums to 1.0). Empty list → empty list.
    """
    if not scores:
        return []
    max_score = max(scores)
    exps = [math.exp(s - max_score) for s in scores]
    total = sum(exps)
    if total == 0.0:
        return [1.0 / len(scores)] * len(scores)
    return [e / total for e in exps]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns 0.0 when either vector is the zero vector (avoids division
    by zero; semantically: no directional similarity can be inferred).

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Similarity in [-1.0, 1.0]. 0.0 if either vector is zero.
    """
    if len(a) != len(b):
        raise ValueError(f"Vector length mismatch: {len(a)} vs {len(b)}")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    # Clamp to [-1, 1] to handle floating-point drift
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def weighted_aggregate(scores: list[float], weights: list[float]) -> float:
    """Compute a weighted sum of scores.

    Args:
        scores: Per-item scores.
        weights: Per-item weights (need not sum to 1).

    Returns:
        Scalar weighted sum. 0.0 for empty inputs.

    Raises:
        ValueError: If scores and weights have different lengths.
    """
    if len(scores) != len(weights):
        raise ValueError(
            f"scores and weights must have equal length: {len(scores)} vs {len(weights)}"
        )
    if not scores:
        return 0.0
    return sum(s * w for s, w in zip(scores, weights))
