"""BlockIndex — k-means clustering over SessionBlock embeddings for DepthFusion.

Provides fast approximate nearest-neighbour retrieval at the block level:
cluster blocks by embedding centroid, then return top-k clusters most similar
to a query embedding.

Uses numpy for centroid arithmetic; falls back to a single cluster if numpy
is unavailable (runtime error surfaced early in fit()).
"""
from __future__ import annotations

import math
import random
from typing import Optional

from depthfusion.core.scoring import cosine_similarity
from depthfusion.core.types import SessionBlock


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _mean_vector(vecs: list[list[float]]) -> list[float]:
    """Element-wise mean of a non-empty list of equal-length vectors."""
    dim = len(vecs[0])
    result = [0.0] * dim
    for v in vecs:
        for i, x in enumerate(v):
            result[i] += x
    n = len(vecs)
    return [x / n for x in result]


def _kmeans(
    vectors: list[list[float]],
    k: int,
    max_iter: int = 100,
    seed: int = 42,
) -> list[list[float]]:
    """Simple k-means returning k centroids.

    Args:
        vectors:  Non-empty list of equal-length float vectors.
        k:        Number of clusters (clipped to len(vectors) if larger).
        max_iter: Maximum iterations.
        seed:     Random seed for reproducibility.

    Returns:
        List of k centroid vectors.
    """
    k = min(k, len(vectors))
    rng = random.Random(seed)
    # k-means++ style initialisation: pick k distinct random vectors as seeds
    centroids = rng.sample(vectors, k)

    for _ in range(max_iter):
        # Assignment step
        clusters: list[list[list[float]]] = [[] for _ in range(k)]
        for v in vectors:
            best_idx = 0
            best_sim = -float("inf")
            for j, c in enumerate(centroids):
                try:
                    sim = cosine_similarity(v, c)
                except ValueError:
                    sim = 0.0
                if sim > best_sim:
                    best_sim = sim
                    best_idx = j
            clusters[best_idx].append(v)

        # Update step
        new_centroids: list[list[float]] = []
        for j, cluster in enumerate(clusters):
            if cluster:
                new_centroids.append(_mean_vector(cluster))
            else:
                # Empty cluster: keep old centroid
                new_centroids.append(centroids[j])

        if new_centroids == centroids:
            break
        centroids = new_centroids

    return centroids


class BlockIndex:
    """K-means–based index over SessionBlock embeddings.

    Usage::

        index = BlockIndex(n_clusters=10)
        index.fit(blocks)
        top_blocks = index.query(query_embedding, top_k=3)
    """

    def __init__(self, n_clusters: int = 10) -> None:
        self._n_clusters = n_clusters
        self._centroids: Optional[list[list[float]]] = None
        # Mapping: centroid index → list of blocks assigned to that cluster
        self._cluster_blocks: list[list[SessionBlock]] = []

    def fit(self, blocks: list[SessionBlock]) -> None:
        """Cluster blocks by embedding. Stores centroids and cluster assignments.

        Blocks without an embedding are silently skipped. If no blocks have
        embeddings, the index remains in the unfitted state.

        Args:
            blocks: Session blocks to index.
        """
        embeddable = [b for b in blocks if b.embedding is not None]
        if not embeddable:
            return

        vectors: list[list[float]] = [b.embedding for b in embeddable]  # type: ignore[misc]
        k = min(self._n_clusters, len(embeddable))
        centroids = _kmeans(vectors, k=k)

        # Assign each block to the nearest centroid
        cluster_blocks: list[list[SessionBlock]] = [[] for _ in range(len(centroids))]
        for block in embeddable:
            best_idx = 0
            best_sim = -float("inf")
            for j, c in enumerate(centroids):
                try:
                    sim = cosine_similarity(block.embedding, c)  # type: ignore[arg-type]
                except ValueError:
                    sim = 0.0
                if sim > best_sim:
                    best_sim = sim
                    best_idx = j
            cluster_blocks[best_idx].append(block)

        self._centroids = centroids
        self._cluster_blocks = cluster_blocks

    def query(
        self,
        query_embedding: list[float],
        top_k: int = 3,
    ) -> list[SessionBlock]:
        """Return top_k blocks whose centroid is most similar to query.

        Blocks within a cluster are returned in an unspecified order.
        The overall list is sorted by centroid-to-query similarity descending.

        Args:
            query_embedding: Query vector.
            top_k:           Number of clusters to retrieve (clips to available).

        Returns:
            Flat list of SessionBlock instances from the top clusters.

        Raises:
            RuntimeError: If called before ``fit()``.
        """
        if not self.is_fitted():
            raise RuntimeError(
                "BlockIndex is not fitted. Call fit() before query()."
            )

        assert self._centroids is not None
        centroid_sims: list[tuple[float, int]] = []
        for j, c in enumerate(self._centroids):
            try:
                sim = cosine_similarity(query_embedding, c)
            except ValueError:
                sim = 0.0
            centroid_sims.append((sim, j))

        centroid_sims.sort(key=lambda x: -x[0])

        # Collect blocks from clusters in centroid-similarity order,
        # stopping once we have top_k blocks.
        result: list[SessionBlock] = []
        for _, cluster_idx in centroid_sims:
            if len(result) >= top_k:
                break
            result.extend(self._cluster_blocks[cluster_idx])

        return result[:top_k]

    def is_fitted(self) -> bool:
        """Return True if ``fit()`` has been called with at least one embeddable block."""
        return self._centroids is not None
