"""S-226: embedding cosine similarity in MemoryConsolidator."""
from __future__ import annotations

import math
from typing import Optional

import pytest

from depthfusion.cognitive.consolidator import (
    MemoryConsolidator,
    _cosine,
    _token_similarity,
)
from depthfusion.core.memory_object import MemoryObject, MemoryStatus


# ---------------------------------------------------------------------------
# Unit: _cosine helper
# ---------------------------------------------------------------------------

def test_cosine_identical():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite():
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_none_input():
    assert _cosine(None, [1.0]) == 0.0  # type: ignore[arg-type]
    assert _cosine([1.0], None) == 0.0  # type: ignore[arg-type]


def test_cosine_empty_input():
    assert _cosine([], []) == 0.0


def test_cosine_mismatched_length():
    assert _cosine([1.0, 0.0], [1.0]) == 0.0


def test_cosine_zero_norm():
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# Unit: _token_similarity still works (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_token_similarity_identical():
    assert _token_similarity("hello world", "hello world") == pytest.approx(1.0)


def test_token_similarity_disjoint():
    assert _token_similarity("foo bar", "baz qux") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Integration: MemoryConsolidator with embed_fn
# ---------------------------------------------------------------------------

def _make_memory(content: str, mid: str = "m1") -> MemoryObject:
    from depthfusion.core.memory_object import MemoryObject, MemoryType
    return MemoryObject(
        id=mid,
        project_id="proj",
        type=MemoryType.SEMANTIC,
        content=content,
        summary="",
    )


def _unit_vec(dim: int, index: int) -> list[float]:
    v = [0.0] * dim
    v[index] = 1.0
    return v


def test_embedding_path_detects_near_duplicate():
    """embed_fn that returns nearly identical vectors causes merge candidate."""
    # Two memories that are lexically different but semantically "same"
    m1 = _make_memory("apple fruit", "m1")
    m2 = _make_memory("orange vegetable", "m2")

    # Embed fn returns identical vectors → cosine = 1.0 > threshold 0.92
    def embed_fn(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]] * len(texts)

    consolidator = MemoryConsolidator(merge_threshold=0.92, embed_fn=embed_fn)
    result = consolidator.find_near_duplicates([m1, m2])
    assert len(result.merge_candidates) == 1


def test_embedding_path_no_false_positives():
    """Orthogonal vectors produce no merge candidates even at low threshold."""
    m1 = _make_memory("hello world", "m1")
    m2 = _make_memory("goodbye mars", "m2")

    call_count = [0]

    def embed_fn(texts: list[str]) -> list[list[float]]:
        call_count[0] += 1
        # Orthogonal: m1 → [1,0], m2 → [0,1]
        return [_unit_vec(2, i) for i in range(len(texts))]

    consolidator = MemoryConsolidator(merge_threshold=0.5, embed_fn=embed_fn)
    result = consolidator.find_near_duplicates([m1, m2])
    assert result.merge_candidates == []
    assert call_count[0] == 1  # batch called once


def test_embed_fn_called_once_for_batch():
    """All memories are embedded in a single batch call, not per-pair."""
    mems = [_make_memory(f"mem {i}", f"m{i}") for i in range(5)]
    call_count = [0]

    def embed_fn(texts: list[str]) -> list[list[float]]:
        call_count[0] += 1
        return [[0.0]] * len(texts)

    consolidator = MemoryConsolidator(embed_fn=embed_fn)
    consolidator.find_near_duplicates(mems)
    assert call_count[0] == 1


def test_fallback_to_token_when_embed_fn_none():
    """Without embed_fn, token Jaccard is used (existing behaviour preserved)."""
    m1 = _make_memory("the quick brown fox", "m1")
    m2 = _make_memory("the quick brown fox", "m2")  # identical → Jaccard 1.0

    consolidator = MemoryConsolidator(merge_threshold=0.92, embed_fn=None)
    result = consolidator.find_near_duplicates([m1, m2])
    assert len(result.merge_candidates) == 1


def test_fallback_when_embed_fn_raises():
    """If embed_fn raises, falls back to token Jaccard silently."""
    m1 = _make_memory("hello hello hello", "m1")
    m2 = _make_memory("hello hello hello", "m2")

    def bad_embed(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embed service down")

    consolidator = MemoryConsolidator(merge_threshold=0.92, embed_fn=bad_embed)
    # Should not raise; falls back to token similarity
    result = consolidator.find_near_duplicates([m1, m2])
    # Token similarity of identical strings = 1.0 → merge candidate
    assert len(result.merge_candidates) == 1


def test_fallback_when_embed_fn_returns_none():
    """If embed_fn returns None, falls back to token Jaccard."""
    m1 = _make_memory("hello hello", "m1")
    m2 = _make_memory("hello hello", "m2")

    def none_embed(texts: list[str]) -> Optional[list[list[float]]]:
        return None

    consolidator = MemoryConsolidator(merge_threshold=0.92, embed_fn=none_embed)
    result = consolidator.find_near_duplicates([m1, m2])
    assert len(result.merge_candidates) == 1


def test_pinned_memories_excluded_from_embedding():
    """Pinned memories are never passed to embed_fn or considered for merge."""
    m_pinned = _make_memory("secret content", "pinned")
    m_pinned.pinned = True
    m_other = _make_memory("other content", "m2")

    embedded_texts: list[str] = []

    def tracking_embed(texts: list[str]) -> list[list[float]]:
        embedded_texts.extend(texts)
        return [[1.0, 0.0]] * len(texts)

    consolidator = MemoryConsolidator(embed_fn=tracking_embed)
    result = consolidator.find_near_duplicates([m_pinned, m_other])
    assert "secret content" not in embedded_texts
    assert result.merge_candidates == []
