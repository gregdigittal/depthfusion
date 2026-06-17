# tests/test_graph/test_builder.py
"""T-618: DocumentEntityBuilder — entity extraction from document chunks.

Verifies:
  1. Entities are extracted from sample document text.
  2. acl_allow is inherited from the supplied source ACL (T-619).
  3. The builder is offline-safe (no crash / no network) when the LLM is absent.
  4. The `build` alias works identically to `extract`.
"""
from unittest.mock import MagicMock

import pytest  # noqa: F401

from depthfusion.graph.builder import DocumentEntityBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_backend(json_response: str) -> MagicMock:
    """Return a mock LLMBackend whose complete() returns `json_response`."""
    mock = MagicMock()
    mock.healthy.return_value = True
    mock.complete.return_value = json_response
    return mock


SAMPLE_CHUNK = (
    "The RRF fusion algorithm is used by RecallPipeline. "
    "TierManager handles storage tiers. See hybrid.py for details."
)

DOC_ACL = ["acme-corp", "legal-team"]


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------

def test_builder_extracts_entities_from_sample_text():
    """Regex path (no LLM): at least one entity is returned from SAMPLE_CHUNK."""
    builder = DocumentEntityBuilder(project="acme")
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md")
    assert len(entities) > 0


def test_builder_extracts_camel_case_class():
    """RegexExtractor surfaces CamelCase classes even without an LLM."""
    builder = DocumentEntityBuilder(project="acme")
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md")
    names = [e.name for e in entities]
    assert "TierManager" in names or "RecallPipeline" in names


def test_builder_with_llm_extracts_concept_entities():
    """LLM path: concept-type entities surface when backend is available."""
    backend = _mock_backend('[{"name": "RRF fusion", "type": "concept"}]')
    builder = DocumentEntityBuilder(project="acme", haiku_backend=backend)
    assert builder.is_available() is True

    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md")
    names = [e.name for e in entities]
    assert "RRF fusion" in names


def test_builder_returns_empty_list_for_empty_input():
    """Empty chunk text → no entities, no crash."""
    builder = DocumentEntityBuilder(project="acme")
    assert builder.extract("", source_file="docs/arch.md") == []
    assert builder.extract("   ", source_file="docs/arch.md") == []


# ---------------------------------------------------------------------------
# ACL inheritance (T-619)
# ---------------------------------------------------------------------------

def test_extracted_entities_inherit_source_acl():
    """Every entity must carry the source document's ACL in metadata."""
    builder = DocumentEntityBuilder(project="acme")
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/contract.md", acl_allow=DOC_ACL)
    assert entities
    for entity in entities:
        assert entity.metadata["acl_allow"] == DOC_ACL


def test_llm_entities_inherit_source_acl():
    """LLM-extracted entities also carry the source document's ACL."""
    backend = _mock_backend('[{"name": "NDA clause", "type": "concept"}]')
    builder = DocumentEntityBuilder(project="acme", haiku_backend=backend)
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/contract.md", acl_allow=DOC_ACL)
    assert entities
    for entity in entities:
        assert entity.metadata["acl_allow"] == DOC_ACL


def test_acl_falls_back_to_project_when_not_supplied():
    """No ACL provided → entities scoped to [project] (backward-compat)."""
    builder = DocumentEntityBuilder(project="acme")
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md")
    assert entities
    for entity in entities:
        assert entity.metadata["acl_allow"] == ["acme"]


def test_acl_is_not_shared_reference():
    """Two entities must not share a mutable ACL list reference."""
    builder = DocumentEntityBuilder(project="acme")
    doc_acl = ["acme-corp"]
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md", acl_allow=doc_acl)
    assert len(entities) >= 2
    entities[0].metadata["acl_allow"].append("mutated")
    assert "mutated" not in entities[1].metadata["acl_allow"]
    # Caller's original list is untouched.
    assert doc_acl == ["acme-corp"]


# ---------------------------------------------------------------------------
# Offline / degraded-mode safety
# ---------------------------------------------------------------------------

def test_builder_is_offline_safe_without_key(monkeypatch):
    """No backend + HAIKU_ENABLED=false → regex-only, no crash."""
    monkeypatch.setenv("DEPTHFUSION_HAIKU_ENABLED", "false")
    builder = DocumentEntityBuilder(project="acme")
    assert builder.is_available() is False
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md")
    # Regex still works.
    assert len(entities) > 0


def test_builder_degrades_gracefully_on_unhealthy_backend():
    """A present-but-unhealthy backend falls back to regex cleanly."""
    backend = MagicMock()
    backend.healthy.return_value = False
    builder = DocumentEntityBuilder(project="acme", haiku_backend=backend)
    assert builder.is_available() is False
    entities = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md")
    # Regex entities still present.
    assert len(entities) > 0


# ---------------------------------------------------------------------------
# `build` alias
# ---------------------------------------------------------------------------

def test_build_alias_is_identical_to_extract():
    """`build` is an alias for `extract` — same result."""
    backend = _mock_backend('[{"name": "RRF fusion", "type": "concept"}]')
    builder = DocumentEntityBuilder(project="acme", haiku_backend=backend)
    via_extract = builder.extract(SAMPLE_CHUNK, source_file="docs/arch.md", acl_allow=DOC_ACL)
    via_build = builder.build(SAMPLE_CHUNK, source_file="docs/arch.md", acl_allow=DOC_ACL)
    assert {e.entity_id for e in via_extract} == {e.entity_id for e in via_build}
