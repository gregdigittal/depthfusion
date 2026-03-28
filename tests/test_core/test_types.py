"""Tests for core/types.py — dataclasses and Protocols."""
from typing import Protocol

from depthfusion.core.types import (
    ContextItem,
    EmbeddingProvider,
    FeedbackEntry,
    RetrievedChunk,
    SessionBlock,
    StorageBackend,
)


class TestRetrievedChunk:
    def test_required_fields(self):
        chunk = RetrievedChunk(
            chunk_id="c1",
            content="Hello world",
            source="session_file",
            score=0.85,
        )
        assert chunk.chunk_id == "c1"
        assert chunk.content == "Hello world"
        assert chunk.source == "session_file"
        assert chunk.score == 0.85

    def test_optional_metadata_defaults_empty(self):
        chunk = RetrievedChunk(chunk_id="c2", content="x", source="s", score=0.5)
        assert chunk.metadata == {}

    def test_rank_defaults_to_none(self):
        chunk = RetrievedChunk(chunk_id="c3", content="x", source="s", score=0.5)
        assert chunk.rank is None

    def test_score_can_be_zero(self):
        chunk = RetrievedChunk(chunk_id="c4", content="x", source="s", score=0.0)
        assert chunk.score == 0.0


class TestSessionBlock:
    def test_required_fields(self):
        block = SessionBlock(
            session_id="sess-001",
            block_index=0,
            content="Some session content",
            tags=["python", "depthfusion"],
        )
        assert block.session_id == "sess-001"
        assert block.block_index == 0
        assert block.tags == ["python", "depthfusion"]

    def test_relevance_score_defaults_to_zero(self):
        block = SessionBlock(session_id="s", block_index=0, content="x", tags=[])
        assert block.relevance_score == 0.0

    def test_embedding_defaults_to_none(self):
        block = SessionBlock(session_id="s", block_index=0, content="x", tags=[])
        assert block.embedding is None


class TestContextItem:
    def test_required_fields(self):
        item = ContextItem(
            item_id="i1",
            content="Some context",
            source_agent="social-media-agent",
            tags=["fintech"],
        )
        assert item.item_id == "i1"
        assert item.source_agent == "social-media-agent"

    def test_ttl_defaults_to_none(self):
        item = ContextItem(item_id="i", content="x", source_agent="a", tags=[])
        assert item.ttl_seconds is None

    def test_priority_defaults_to_normal(self):
        item = ContextItem(item_id="i", content="x", source_agent="a", tags=[])
        assert item.priority == "normal"


class TestFeedbackEntry:
    def test_required_fields(self):
        entry = FeedbackEntry(
            query="search query",
            source="memory",
            chunk_id="c1",
            relevant=True,
        )
        assert entry.query == "search query"
        assert entry.source == "memory"
        assert entry.relevant is True

    def test_timestamp_defaults_to_none(self):
        entry = FeedbackEntry(query="q", source="s", chunk_id="c", relevant=False)
        assert entry.timestamp is None


class TestProtocols:
    def test_embedding_provider_is_protocol(self):
        assert issubclass(EmbeddingProvider, Protocol)

    def test_storage_backend_is_protocol(self):
        assert issubclass(StorageBackend, Protocol)

    def test_embedding_provider_runtime_checkable(self):
        """A class with embed() method satisfies the protocol."""
        class FakeEmbedder:
            def embed(self, text: str) -> list[float]:
                return [0.1, 0.2, 0.3]
        embedder = FakeEmbedder()
        assert isinstance(embedder, EmbeddingProvider)

    def test_storage_backend_runtime_checkable(self):
        """A class with get/put/delete satisfies StorageBackend."""
        class FakeStore:
            def get(self, key: str):
                return None
            def put(self, key: str, value) -> None:
                pass
            def delete(self, key: str) -> None:
                pass
        store = FakeStore()
        assert isinstance(store, StorageBackend)
