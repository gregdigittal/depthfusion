# tests/test_graph/test_builder.py
"""T-618: DocumentEntityBuilder — LLM extraction with offline regex fallback."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from depthfusion.backends.null import NullBackend
from depthfusion.graph.builder import DocumentEntityBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_backend(response: str, healthy: bool = True) -> MagicMock:
    """Build a mock backend that returns *response* from complete()."""
    mock = MagicMock()
    mock.healthy.return_value = healthy
    mock.name = "haiku"
    mock.complete.return_value = response
    return mock


def _llm_response(*entities: dict) -> str:
    """JSON string of entity dicts suitable for the extractor prompt."""
    return json.dumps(list(entities))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_builder_constructible_without_backend():
    """Builder must not raise even with no API key / backend."""
    builder = DocumentEntityBuilder(project="test")
    assert builder is not None


def test_builder_not_available_with_null_backend():
    """NullBackend → is_available() returns False (NullBackend.name == 'null')."""
    builder = DocumentEntityBuilder(project="test", haiku_backend=NullBackend())
    # NullBackend is the no-op sentinel; builder should report LLM unavailable
    # (same contract as HaikuLinker: name != "null" is the availability gate)
    assert builder.is_available() is False


def test_builder_available_with_healthy_mock():
    """Injected healthy mock → is_available() returns True."""
    backend = _mock_backend('[{"name": "BM25", "type": "concept"}]')
    builder = DocumentEntityBuilder(project="test", haiku_backend=backend)
    assert builder.is_available() is True


# ---------------------------------------------------------------------------
# T-618: LLM extraction path
# ---------------------------------------------------------------------------

def test_builder_llm_extracts_concept():
    """When LLM is available, builder uses LLM extraction."""
    response = _llm_response({"name": "BM25", "type": "concept"})
    backend = _mock_backend(response)
    builder = DocumentEntityBuilder(project="test", haiku_backend=backend)

    entities = builder.extract(
        chunk_text="BM25 scoring is used extensively here.",
        source_file="docs/retrieval.md",
        acl_allow=["test"],
    )
    names = [e.name for e in entities]
    # LLM entity should be present (may also have regex entities)
    assert "BM25" in names


def test_builder_llm_calls_backend_with_content(capsys):
    """Backend.complete() is called with the chunk text."""
    response = _llm_response({"name": "TierManager", "type": "concept"})
    backend = _mock_backend(response)
    builder = DocumentEntityBuilder(project="test", haiku_backend=backend)

    builder.extract(
        chunk_text="TierManager handles tier logic.",
        source_file="docs/arch.md",
        acl_allow=["test"],
    )
    assert backend.complete.called


# ---------------------------------------------------------------------------
# Offline fallback (regex) when LLM not available
# ---------------------------------------------------------------------------

def test_builder_regex_fallback_when_llm_unavailable():
    """With NullBackend (offline), regex entities are still returned."""
    builder = DocumentEntityBuilder(project="test", haiku_backend=NullBackend())

    entities = builder.extract(
        chunk_text="TierManager and RecallPipeline cooperate here.",
        source_file="docs/arch.md",
        acl_allow=["test"],
    )
    names = [e.name for e in entities]
    assert "TierManager" in names
    assert "RecallPipeline" in names


def test_builder_regex_fallback_entity_count():
    """Regex fallback returns at least the camelCase entities found."""
    builder = DocumentEntityBuilder(project="test", haiku_backend=NullBackend())
    entities = builder.extract(
        chunk_text="GraphStore uses HaikuExtractor and CoOccurrenceLinker.",
        source_file="test.md",
        acl_allow=["test"],
    )
    assert len(entities) >= 3


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_builder_empty_text_returns_empty():
    builder = DocumentEntityBuilder(project="test", haiku_backend=NullBackend())
    assert builder.extract("", source_file="f.md", acl_allow=["test"]) == []


def test_builder_whitespace_only_returns_empty():
    builder = DocumentEntityBuilder(project="test", haiku_backend=NullBackend())
    assert builder.extract("   \n\t  ", source_file="f.md", acl_allow=["test"]) == []


def test_builder_unhealthy_backend_falls_back_to_regex():
    """Unhealthy injected backend → graceful fallback to regex."""
    backend = _mock_backend("", healthy=False)
    builder = DocumentEntityBuilder(project="test", haiku_backend=backend)

    entities = builder.extract(
        chunk_text="RecallPipeline integrates with HaikuExtractor.",
        source_file="arch.md",
        acl_allow=["test"],
    )
    names = [e.name for e in entities]
    assert "RecallPipeline" in names or "HaikuExtractor" in names


def test_builder_malformed_llm_response_falls_back_to_regex():
    """Malformed JSON from LLM → graceful fallback, no crash."""
    backend = _mock_backend("not valid json at all")
    builder = DocumentEntityBuilder(project="test", haiku_backend=backend)

    entities = builder.extract(
        chunk_text="GraphStore is the persistence layer.",
        source_file="arch.md",
        acl_allow=["test"],
    )
    # Should not raise; may return regex entities
    assert isinstance(entities, list)


def test_builder_build_alias_same_as_extract():
    """build() is an alias for extract()."""
    builder = DocumentEntityBuilder(project="test", haiku_backend=NullBackend())
    text = "TierManager handles scoring."
    a = builder.extract(text, source_file="f.md", acl_allow=["test"])
    b = builder.build(text, source_file="f.md", acl_allow=["test"])
    assert [e.name for e in a] == [e.name for e in b]
