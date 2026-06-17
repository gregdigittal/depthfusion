# tests/test_graph/test_extractor.py
from unittest.mock import MagicMock

import pytest  # noqa: F401 — used indirectly via fixtures

from depthfusion.graph.extractor import (
    HaikuExtractor,
    RegexExtractor,
    confidence_merge,
    make_entity_id,
)
from depthfusion.graph.types import Entity


def _mock_backend_with_response(text: str):
    """Build a mock LLMBackend whose `complete()` returns `text`."""
    mock = MagicMock()
    mock.healthy.return_value = True
    mock.complete.return_value = text
    return mock


SAMPLE_TEXT = """
## Architecture

The TierManager class manages storage tiers.
rrf_fuse() is called from RecallPipeline.
See hybrid.py for the main pipeline.
BM25 scoring is the baseline retrieval method.
"""


def test_regex_extracts_camel_case_class():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    names = [e.name for e in entities]
    assert "TierManager" in names
    assert "RecallPipeline" in names


def test_regex_extracts_snake_case_function():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    names = [e.name for e in entities]
    assert "rrf_fuse()" in names


def test_regex_extracts_file_reference():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    names = [e.name for e in entities]
    assert "hybrid.py" in names


def test_regex_confidence_is_1_0():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    for e in entities:
        assert e.confidence == 1.0


def test_regex_entity_type_class():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    tier_entity = next(e for e in entities if e.name == "TierManager")
    assert tier_entity.type == "class"


def test_regex_entity_type_function():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    fn_entity = next(e for e in entities if e.name == "rrf_fuse()")
    assert fn_entity.type == "function"


def test_regex_entity_type_file():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    file_entity = next(e for e in entities if e.name == "hybrid.py")
    assert file_entity.type == "file"


def test_make_entity_id_is_12_chars():
    eid = make_entity_id("TierManager", "class", "depthfusion")
    assert len(eid) == 12


def test_make_entity_id_is_deterministic():
    a = make_entity_id("TierManager", "class", "depthfusion")
    b = make_entity_id("TierManager", "class", "depthfusion")
    assert a == b


def test_make_entity_id_differs_by_project():
    a = make_entity_id("TierManager", "class", "depthfusion")
    b = make_entity_id("TierManager", "class", "skillforge")
    assert a != b


def test_haiku_extractor_returns_entities_when_available():
    backend = _mock_backend_with_response('[{"name": "BM25 scoring", "type": "concept"}]')
    extractor = HaikuExtractor(project="depthfusion", backend=backend)

    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    assert any(e.name == "BM25 scoring" for e in entities)


def test_haiku_extractor_confidence_in_range():
    backend = _mock_backend_with_response('[{"name": "BM25 scoring", "type": "concept"}]')
    extractor = HaikuExtractor(project="depthfusion", backend=backend)

    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    for e in entities:
        assert 0.70 <= e.confidence <= 0.95


def test_haiku_extractor_returns_empty_when_unavailable(monkeypatch):
    """No backend injected, HAIKU_ENABLED explicitly off → no backend
    resolved, is_available() False → empty result. Explicit env cleanup
    makes this test order-independent (other tests may have set the flag).
    """
    monkeypatch.setenv("DEPTHFUSION_HAIKU_ENABLED", "false")
    extractor = HaikuExtractor(project="depthfusion")
    # _backend was never constructed because HAIKU_ENABLED is false
    assert extractor._backend is None
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    assert entities == []


def test_haiku_extractor_handles_malformed_json():
    backend = _mock_backend_with_response("not json")
    extractor = HaikuExtractor(project="depthfusion", backend=backend)
    # Should not raise
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    assert entities == []


def test_haiku_extractor_handles_empty_response():
    """Backend returns empty string (e.g. NullBackend) → no entities."""
    backend = _mock_backend_with_response("")
    extractor = HaikuExtractor(project="depthfusion", backend=backend)
    assert extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md") == []


def test_haiku_extractor_skips_entities_with_empty_name():
    """Defensive: malformed items with blank names get dropped."""
    backend = _mock_backend_with_response(
        '[{"name": "", "type": "concept"}, {"name": "Real", "type": "concept"}]'
    )
    extractor = HaikuExtractor(project="depthfusion", backend=backend)
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    names = [e.name for e in entities]
    assert names == ["Real"]


def test_haiku_extractor_caps_at_10_entities():
    """Defensive: even if the model returns 20 items, only 10 are kept."""
    items = ",".join(f'{{"name": "E{i}", "type": "concept"}}' for i in range(20))
    backend = _mock_backend_with_response(f"[{items}]")
    extractor = HaikuExtractor(project="depthfusion", backend=backend)
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    assert len(entities) == 10


def test_confidence_merge_deduplicates():
    regex_e = Entity(
        entity_id=make_entity_id("TierManager", "class", "depthfusion"),
        name="TierManager", type="class", project="depthfusion",
        source_files=["memory/arch.md"], confidence=1.0,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    haiku_e = Entity(
        entity_id=make_entity_id("TierManager", "class", "depthfusion"),
        name="TierManager", type="class", project="depthfusion",
        source_files=["memory/arch.md"], confidence=0.85,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    merged = confidence_merge([regex_e], [haiku_e])
    # Regex (1.0) takes precedence over haiku duplicate
    tier_entities = [e for e in merged if e.name == "TierManager"]
    assert len(tier_entities) == 1
    assert tier_entities[0].confidence == 1.0


def test_confidence_merge_keeps_haiku_only_entities():
    haiku_e = Entity(
        entity_id=make_entity_id("BM25 scoring", "concept", "depthfusion"),
        name="BM25 scoring", type="concept", project="depthfusion",
        source_files=["memory/arch.md"], confidence=0.85,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    merged = confidence_merge([], [haiku_e])
    assert len(merged) == 1
    assert merged[0].name == "BM25 scoring"


def test_below_threshold_entities_included_in_output():
    """Entities below 0.70 are stored but callers filter for query expansion."""
    haiku_e = Entity(
        entity_id=make_entity_id("vague term", "concept", "depthfusion"),
        name="vague term", type="concept", project="depthfusion",
        source_files=[], confidence=0.55,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    merged = confidence_merge([], [haiku_e])
    assert len(merged) == 1
    assert merged[0].confidence < 0.70
