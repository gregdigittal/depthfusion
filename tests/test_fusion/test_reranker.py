"""Tests for fusion/reranker.py — Pluggable reranker protocol."""
from __future__ import annotations

from depthfusion.core.types import RetrievedChunk
from depthfusion.fusion.reranker import LLMReranker, PassthroughReranker, Reranker


def make_chunk(chunk_id: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        content=f"content of {chunk_id}",
        source="memory",
        score=score,
    )


class TestPassthroughReranker:
    def test_satisfies_reranker_protocol(self):
        pr = PassthroughReranker()
        assert isinstance(pr, Reranker)

    def test_returns_same_chunks_same_order(self):
        chunks = [make_chunk("a"), make_chunk("b"), make_chunk("c")]
        pr = PassthroughReranker()
        result = pr.rerank("test query", chunks)
        assert result == chunks

    def test_empty_chunks_returns_empty(self):
        pr = PassthroughReranker()
        assert pr.rerank("query", []) == []

    def test_single_chunk_returned(self):
        chunk = make_chunk("solo")
        pr = PassthroughReranker()
        result = pr.rerank("query", [chunk])
        assert result == [chunk]

    def test_returns_same_list_object(self):
        """PassthroughReranker should return the same list (identity, not copy)."""
        chunks = [make_chunk("x")]
        pr = PassthroughReranker()
        result = pr.rerank("q", chunks)
        assert result is chunks


class TestLLMReranker:
    def test_satisfies_reranker_protocol(self):
        lr = LLMReranker()
        assert isinstance(lr, Reranker)

    def test_falls_back_to_passthrough(self):
        chunks = [make_chunk("a"), make_chunk("b")]
        lr = LLMReranker()
        result = lr.rerank("query", chunks)
        assert result == chunks

    def test_empty_chunks_returns_empty(self):
        lr = LLMReranker()
        assert lr.rerank("query", []) == []

    def test_accepts_custom_model(self):
        lr = LLMReranker(model="opus")
        chunks = [make_chunk("a")]
        result = lr.rerank("query", chunks)
        assert result == chunks

    def test_logs_warning_on_rerank(self, caplog):
        """LLMReranker logs a warning that it is using passthrough."""
        import logging
        lr = LLMReranker()
        chunks = [make_chunk("a")]
        with caplog.at_level(logging.WARNING):
            lr.rerank("query", chunks)
        assert any("passthrough" in record.message.lower() or "not yet implemented" in record.message.lower()
                   for record in caplog.records)
