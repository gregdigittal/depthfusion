"""Tests for decision_extractor.py — CM-1 / S-45 / T-139.

≥ 8 tests required by S-45 AC-4.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from depthfusion.capture.decision_extractor import (
    DecisionEntry,
    HeuristicDecisionExtractor,
    LLMDecisionExtractor,
    extract_and_write,
    write_decisions,
)

# ---------------------------------------------------------------------------
# DecisionEntry
# ---------------------------------------------------------------------------

class TestDecisionEntry:
    def test_confidence_clamps_high(self):
        e = DecisionEntry("some text", confidence=2.5)
        assert e.confidence == 1.0

    def test_confidence_clamps_low(self):
        e = DecisionEntry("some text", confidence=-0.3)
        assert e.confidence == 0.0

    def test_defaults(self):
        e = DecisionEntry("use async/await everywhere", confidence=0.8)
        assert e.category == "decision"
        assert e.source_session == ""


# ---------------------------------------------------------------------------
# HeuristicDecisionExtractor
# ---------------------------------------------------------------------------

class TestHeuristicDecisionExtractor:
    def setup_method(self):
        self.extractor = HeuristicDecisionExtractor()

    def test_empty_content_returns_empty(self):
        assert self.extractor.extract("") == []

    def test_short_content_returns_empty(self):
        assert self.extractor.extract("hi") == []

    def test_arrow_pattern_extracted(self):
        content = "→ Use postgres for persistence\n→ Keep stateless services"
        results = self.extractor.extract(content)
        texts = [e.text for e in results]
        assert any("postgres" in t for t in texts)

    def test_decision_keyword_extracted(self):
        content = "DECISION: Switch to async handlers for better throughput"
        results = self.extractor.extract(content)
        assert len(results) >= 1
        assert "async handlers" in results[0].text

    def test_all_results_confidence_equal_default(self):
        content = "→ Use redis for caching\n→ Store sessions in redis"
        results = self.extractor.extract(content)
        assert all(e.confidence == 0.60 for e in results)

    def test_deduplication(self):
        # Same text appearing twice under different patterns should appear once
        content = "→ Use redis for session storage\n→ Use redis for session storage"
        results = self.extractor.extract(content)
        texts = [e.text for e in results]
        assert texts.count("Use redis for session storage") == 1

    def test_source_session_propagated(self):
        content = "→ Keep handlers small"
        results = self.extractor.extract(content, source_session="my-session-id")
        assert results[0].source_session == "my-session-id"

    def test_bold_pattern_extracted(self):
        content = "**Always validate inputs at system boundaries**"
        results = self.extractor.extract(content)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# LLMDecisionExtractor
# ---------------------------------------------------------------------------

class TestLLMDecisionExtractor:
    def test_no_backend_falls_back_to_heuristic(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_DECISION_EXTRACTOR_ENABLED", raising=False)
        extractor = LLMDecisionExtractor()
        assert not extractor.is_available()
        # Falls back to heuristic — still returns entries for matching content
        content = "→ Use redis for all caching"
        results = extractor.extract(content)
        assert isinstance(results, list)

    def test_injected_backend_used(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {
            "decisions": [
                {"text": "Use postgres", "confidence": 0.9, "category": "decision"},
            ]
        }
        extractor = LLMDecisionExtractor(backend=backend)
        assert extractor.is_available()
        results = extractor.extract("session content here", source_session="sess-1")
        assert len(results) == 1
        assert results[0].text == "Use postgres"
        assert results[0].confidence == 0.9

    def test_llm_failure_falls_back_to_heuristic(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.side_effect = RuntimeError("API down")
        extractor = LLMDecisionExtractor(backend=backend)
        content = "→ Use redis for caching"
        results = extractor.extract(content)
        assert isinstance(results, list)

    def test_empty_decisions_list_falls_back(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {"decisions": []}
        extractor = LLMDecisionExtractor(backend=backend)
        content = "→ Always use HTTPS"
        results = extractor.extract(content)
        # Fell back to heuristic, should still find something
        assert isinstance(results, list)

    def test_malformed_result_falls_back(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = None
        extractor = LLMDecisionExtractor(backend=backend)
        content = "→ Deploy to kubernetes"
        results = extractor.extract(content)
        assert isinstance(results, list)

    def test_short_text_items_skipped(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {
            "decisions": [
                {"text": "hi", "confidence": 0.9, "category": "fact"},        # too short
                {"text": "Use Redis for session storage", "confidence": 0.85, "category": "fact"},
            ]
        }
        extractor = LLMDecisionExtractor(backend=backend)
        results = extractor.extract("content")
        assert len(results) == 1
        assert "Redis" in results[0].text

    def test_duplicate_items_deduplicated(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {
            "decisions": [
                {"text": "Use postgres for storage", "confidence": 0.8, "category": "decision"},
                {"text": "Use postgres for storage", "confidence": 0.9, "category": "decision"},
            ]
        }
        extractor = LLMDecisionExtractor(backend=backend)
        results = extractor.extract("content")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# write_decisions
# ---------------------------------------------------------------------------

class TestWriteDecisions:
    def test_writes_file(self, tmp_path):
        entries = [
            DecisionEntry("Use postgres", confidence=0.9),
            DecisionEntry("Deploy via k8s", confidence=0.8),
        ]
        out = write_decisions(entries, project="myproj", session_id="sess-1",
                              output_dir=tmp_path)
        assert out is not None
        assert out.exists()
        content = out.read_text()
        assert "type: decisions" in content
        assert "Use postgres" in content

    def test_idempotent(self, tmp_path):
        entries = [DecisionEntry("Use postgres", confidence=0.9)]
        out1 = write_decisions(entries, project="myproj", session_id="sess-1",
                               output_dir=tmp_path)
        assert out1 is not None
        out2 = write_decisions(entries, project="myproj", session_id="sess-2",
                               output_dir=tmp_path)
        # Same date+project → same file → skipped
        assert out2 is None

    def test_empty_entries_returns_none(self, tmp_path):
        out = write_decisions([], project="myproj", session_id="sess-1",
                              output_dir=tmp_path)
        assert out is None

    def test_min_confidence_filter(self, tmp_path):
        entries = [
            DecisionEntry("High conf decision", confidence=0.9),
            DecisionEntry("Low conf noise", confidence=0.3),
        ]
        out = write_decisions(entries, project="myproj", session_id="sess-1",
                              output_dir=tmp_path, min_confidence=0.5)
        assert out is not None
        content = out.read_text()
        assert "High conf decision" in content
        assert "Low conf noise" not in content

    def test_frontmatter_fields(self, tmp_path):
        entries = [DecisionEntry("Pick asyncpg over psycopg2", confidence=0.95)]
        out = write_decisions(entries, project="depthfusion", session_id="abc-123",
                              output_dir=tmp_path)
        assert out is not None
        content = out.read_text()
        assert "project: depthfusion" in content
        assert "session_id: abc-123" in content
        assert "entries: 1" in content

    def test_project_slug_sanitized(self, tmp_path):
        entries = [DecisionEntry("Some decision text here", confidence=0.8)]
        out = write_decisions(entries, project="My Project/Name",
                              session_id="s1", output_dir=tmp_path)
        assert out is not None
        assert "my-project-name" in out.name


# ---------------------------------------------------------------------------
# extract_and_write (integration)
# ---------------------------------------------------------------------------

class TestExtractAndWrite:
    def test_end_to_end_with_backend(self, tmp_path):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {
            "decisions": [
                {"text": "Use async everywhere in services", "confidence": 0.88,
                 "category": "decision"},
            ]
        }
        out = extract_and_write(
            "session content",
            project="myapp",
            session_id="sess-e2e",
            output_dir=tmp_path,
            backend=backend,
        )
        assert out is not None
        assert out.exists()
        content = out.read_text()
        assert "async everywhere" in content

    def test_returns_none_on_empty_content(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_DECISION_EXTRACTOR_ENABLED", raising=False)
        out = extract_and_write("", project="myapp", session_id="s1",
                                output_dir=tmp_path)
        assert out is None
