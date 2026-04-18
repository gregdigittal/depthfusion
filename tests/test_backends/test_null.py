# tests/test_backends/test_null.py
"""NullBackend behaviour tests.

NullBackend is the terminal fallback in every capability chain. If its
behaviour regresses, every downstream capability that cascades to it
breaks in subtly different ways — so every method is tested.

Backlog: T-117, T-122.
"""
from __future__ import annotations

from depthfusion.backends.null import NullBackend


def test_null_is_always_healthy():
    """Even with no credentials, no SDK, offline host — Null is healthy.
    This is the contract that makes it a safe terminal fallback.
    """
    assert NullBackend().healthy() is True


def test_null_name_is_literal_null():
    """Stable identifier for audit records and factory dispatch."""
    assert NullBackend().name == "null"


def test_null_complete_returns_empty_string():
    assert NullBackend().complete("anything", max_tokens=100) == ""


def test_null_complete_accepts_optional_system_prompt():
    """complete() signature includes an optional system prompt. Null
    ignores it but must accept it without raising.
    """
    assert NullBackend().complete("p", max_tokens=50, system="sys") == ""


def test_null_embed_returns_none_not_empty_list():
    """`None` signals 'embedding unsupported' — distinct from an empty
    embedding list which would be an error. Callers rely on this.
    """
    assert NullBackend().embed(["a", "b"]) is None


def test_null_embed_on_empty_input_still_returns_none():
    assert NullBackend().embed([]) is None


def test_null_rerank_preserves_order_up_to_top_k():
    result = NullBackend().rerank("q", ["a", "b", "c", "d"], top_k=2)
    assert result == [(0, 0.0), (1, 0.0)]


def test_null_rerank_handles_empty_docs():
    """Must not index-error on empty input."""
    assert NullBackend().rerank("q", [], top_k=5) == []


def test_null_rerank_top_k_exceeds_docs_length():
    """top_k=10 with 3 docs yields 3 tuples, not 10. Contract documented in base.py."""
    result = NullBackend().rerank("q", ["a", "b", "c"], top_k=10)
    assert len(result) == 3


def test_null_extract_structured_returns_none():
    assert NullBackend().extract_structured("prompt", {"type": "object"}) is None
