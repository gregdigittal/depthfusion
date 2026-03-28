"""AttnRes-inspired weighted fusion for DepthFusion.

Phase 1: cosine similarity over chunk/block embeddings → softmax → attention weights.
Phase 2: chunk score = base_score × block_weight × source_weight.
Fallback to score-based ordering if no embeddings are provided.

Reference: AttnRes — arXiv:2603.15031 Section 3.2.
"""
from __future__ import annotations

from depthfusion.core.scoring import cosine_similarity, softmax_scores
from depthfusion.core.types import RetrievedChunk, SessionBlock


def compute_block_weights(
    query_embedding: list[float],
    blocks: list[SessionBlock],
) -> list[float]:
    """Cosine similarity of query vs each block embedding → softmax → attention weights.

    Blocks without an embedding are assigned a similarity of 0.0.

    Args:
        query_embedding: Query vector.
        blocks:          Session blocks, each optionally carrying an embedding.

    Returns:
        Softmax-normalised weights, one per block. Empty list if no blocks.
    """
    if not blocks:
        return []

    similarities: list[float] = []
    for block in blocks:
        if block.embedding is None:
            similarities.append(0.0)
        else:
            try:
                similarities.append(cosine_similarity(query_embedding, block.embedding))
            except ValueError:
                similarities.append(0.0)

    return softmax_scores(similarities)


def attnres_fusion(
    chunks: list[RetrievedChunk],
    query_embedding: list[float] | None = None,
    source_weights: dict[str, float] | None = None,
) -> list[RetrievedChunk]:
    """AttnRes-inspired fusion.

    Returns chunks sorted by fused score descending, with ``chunk.score``
    updated to the fused score and ``chunk.rank`` set to the final 1-indexed rank.

    Fusion logic:
    - If ``query_embedding`` is provided and a chunk carries ``metadata["embedding"]``,
      compute cosine similarity → softmax weight to scale the base score.
    - If ``source_weights`` is provided, multiply by the weight for each chunk's source.
    - If no embeddings are available, fall back to ordering by original score.

    Args:
        chunks:          Chunks to fuse.
        query_embedding: Optional query vector for embedding-based weighting.
        source_weights:  Optional per-source multipliers, e.g. {"memory": 2.0}.

    Returns:
        Re-sorted, re-ranked list of RetrievedChunk instances.
    """
    if not chunks:
        return []

    source_weights = source_weights or {}

    # Determine whether embedding-based weighting is applicable
    has_embeddings = query_embedding is not None and any(
        "embedding" in c.metadata for c in chunks
    )

    if has_embeddings:
        # Compute per-chunk cosine similarities
        assert query_embedding is not None  # narrowing for type checker
        raw_sims: list[float] = []
        for chunk in chunks:
            emb = chunk.metadata.get("embedding")
            if emb is not None:
                try:
                    raw_sims.append(cosine_similarity(query_embedding, emb))
                except ValueError:
                    raw_sims.append(0.0)
            else:
                raw_sims.append(0.0)

        attn_weights = softmax_scores(raw_sims)
        # Scale by number of chunks so weights ≥ 1 on average (prevent score collapse)
        n = len(chunks)
        fused_scores = [
            chunk.score * (attn_weights[i] * n) * source_weights.get(chunk.source, 1.0)
            for i, chunk in enumerate(chunks)
        ]
    else:
        # Fallback: multiply original score by source weight only
        fused_scores = [
            chunk.score * source_weights.get(chunk.source, 1.0)
            for chunk in chunks
        ]

    # Pair and sort descending; stable sort for equal scores
    paired = list(zip(fused_scores, chunks))
    paired.sort(key=lambda x: -x[0])

    result: list[RetrievedChunk] = []
    for rank, (score, chunk) in enumerate(paired, start=1):
        chunk.score = score
        chunk.rank = rank
        result.append(chunk)

    return result
