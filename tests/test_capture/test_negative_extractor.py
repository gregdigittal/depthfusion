"""Tests for negative_extractor.py — CM-6 / S-48 / T-148.

≥ 6 tests required by S-48 AC-3.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from depthfusion.capture.negative_extractor import (
    HeuristicNegativeExtractor,
    LLMNegativeExtractor,
    NegativeEntry,
    extract_and_write,
    write_negatives,
)

# ---------------------------------------------------------------------------
# NegativeEntry
# ---------------------------------------------------------------------------

class TestNegativeEntry:
    def test_confidence_clamps_high(self):
        e = NegativeEntry("X", "Y", confidence=3.0)
        assert e.confidence == 1.0

    def test_confidence_clamps_low(self):
        e = NegativeEntry("X", "Y", confidence=-1.0)
        assert e.confidence == 0.0

    def test_defaults(self):
        e = NegativeEntry("JWT RS256", "not supported in v1")
        assert e.confidence == 0.70
        assert e.source_session == ""


# ---------------------------------------------------------------------------
# HeuristicNegativeExtractor
# ---------------------------------------------------------------------------

class TestHeuristicNegativeExtractor:
    def setup_method(self):
        self.extractor = HeuristicNegativeExtractor()

    def test_empty_returns_empty(self):
        assert self.extractor.extract("") == []

    def test_short_returns_empty(self):
        assert self.extractor.extract("no") == []

    def test_did_not_work_pattern(self):
        content = "The redis connection did not work because the port was blocked"
        results = self.extractor.extract(content)
        assert len(results) >= 1

    def test_failed_with_pattern(self):
        content = "The database migration failed with a lock timeout error"
        results = self.extractor.extract(content)
        assert len(results) >= 1

    def test_do_not_pattern(self):
        content = "DO NOT use synchronous requests in the event loop"
        results = self.extractor.extract(content)
        assert len(results) >= 1

    def test_error_pattern(self):
        content = "error: ConnectionRefusedError: localhost:5432 refused"
        results = self.extractor.extract(content)
        assert len(results) >= 1

    def test_deduplication(self):
        content = (
            "redis connection did not work because blocked\n"
            "redis connection did not work because blocked"
        )
        results = self.extractor.extract(content)
        # Should not double-count the same 'what' text
        texts = [e.what for e in results]
        assert len(texts) == len(set(texts))

    def test_source_session_propagated(self):
        content = "DO NOT run migrations without a backup"
        results = self.extractor.extract(content, source_session="sess-x")
        assert results[0].source_session == "sess-x"


# ---------------------------------------------------------------------------
# LLMNegativeExtractor
# ---------------------------------------------------------------------------

class TestLLMNegativeExtractor:
    def test_no_backend_falls_back(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_DECISION_EXTRACTOR_ENABLED", raising=False)
        extractor = LLMNegativeExtractor()
        assert not extractor.is_available()
        content = "The auth service failed with 401"
        results = extractor.extract(content)
        assert isinstance(results, list)

    def test_injected_backend_used(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {
            "negatives": [
                {"what": "JWT RS256", "why": "library not installed", "confidence": 0.85},
            ]
        }
        extractor = LLMNegativeExtractor(backend=backend)
        results = extractor.extract("content", source_session="s1")
        assert len(results) == 1
        assert results[0].what == "JWT RS256"
        assert results[0].why == "library not installed"

    def test_llm_failure_falls_back(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.side_effect = RuntimeError("timeout")
        extractor = LLMNegativeExtractor(backend=backend)
        content = "DO NOT run migrations without backup"
        results = extractor.extract(content)
        assert isinstance(results, list)

    def test_empty_negatives_falls_back(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {"negatives": []}
        extractor = LLMNegativeExtractor(backend=backend)
        content = "The auth service failed with 401 error"
        results = extractor.extract(content)
        assert isinstance(results, list)

    def test_short_what_skipped(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {
            "negatives": [
                {"what": "hi", "why": "too short", "confidence": 0.9},
                {"what": "bcrypt v1 password hashing", "why": "deprecated", "confidence": 0.8},
            ]
        }
        extractor = LLMNegativeExtractor(backend=backend)
        results = extractor.extract("content")
        assert len(results) == 1
        assert "bcrypt" in results[0].what

    def test_malformed_result_falls_back(self):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = None
        extractor = LLMNegativeExtractor(backend=backend)
        content = "error: ConnectionRefused on port 5432"
        results = extractor.extract(content)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# write_negatives
# ---------------------------------------------------------------------------

class TestWriteNegatives:
    def test_writes_file(self, tmp_path):
        entries = [
            NegativeEntry("bcrypt v1", "deprecated API", confidence=0.9),
            NegativeEntry("synchronous redis calls", "blocks event loop", confidence=0.8),
        ]
        out = write_negatives(entries, project="myproj", session_id="sess-1",
                              output_dir=tmp_path)
        assert out is not None
        assert out.exists()
        content = out.read_text()
        assert "type: negative" in content
        assert "bcrypt v1" in content

    def test_idempotent(self, tmp_path):
        entries = [NegativeEntry("bcrypt v1", "deprecated", confidence=0.9)]
        out1 = write_negatives(entries, project="myproj", session_id="s1",
                               output_dir=tmp_path)
        assert out1 is not None
        out2 = write_negatives(entries, project="myproj", session_id="s2",
                               output_dir=tmp_path)
        assert out2 is None  # same date+project → same file → skip

    def test_empty_returns_none(self, tmp_path):
        out = write_negatives([], project="myproj", session_id="sess-1",
                              output_dir=tmp_path)
        assert out is None

    def test_frontmatter_fields(self, tmp_path):
        entries = [NegativeEntry("sync redis", "blocks loop", confidence=0.7)]
        out = write_negatives(entries, project="depthfusion", session_id="abc",
                              output_dir=tmp_path)
        assert out is not None
        content = out.read_text()
        assert "project: depthfusion" in content
        assert "session_id: abc" in content
        assert "type: negative" in content

    def test_filename_format(self, tmp_path):
        entries = [NegativeEntry("old auth lib", "CVE found", confidence=0.95)]
        out = write_negatives(entries, project="myapp", session_id="s1",
                              output_dir=tmp_path)
        assert out is not None
        # Filename: {date}-{slug}-negatives.md
        assert out.name.endswith("-myapp-negatives.md")

    def test_why_included_in_output(self, tmp_path):
        entries = [NegativeEntry("redis v4", "breaking API changes", confidence=0.85)]
        out = write_negatives(entries, project="proj", session_id="s1",
                              output_dir=tmp_path)
        assert out is not None
        content = out.read_text()
        assert "breaking API changes" in content


# ---------------------------------------------------------------------------
# extract_and_write (integration)
# ---------------------------------------------------------------------------

class TestExtractAndWrite:
    def test_end_to_end_with_backend(self, tmp_path):
        backend = MagicMock()
        backend.healthy.return_value = True
        backend.extract_structured.return_value = {
            "negatives": [
                {"what": "bcrypt v1 hashing algorithm",
                 "why": "deprecated, vulnerable to timing attacks",
                 "confidence": 0.9},
            ]
        }
        out = extract_and_write(
            "session content here",
            project="myapp",
            session_id="sess-neg",
            output_dir=tmp_path,
            backend=backend,
        )
        assert out is not None
        content = out.read_text()
        assert "bcrypt v1" in content

    def test_empty_content_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_DECISION_EXTRACTOR_ENABLED", raising=False)
        out = extract_and_write("", project="myapp", session_id="s1",
                                output_dir=tmp_path)
        assert out is None
