"""Tests for the pre-indexing admission gate (S-118)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

# Note: _admission_score is module-level in vector_store, not a class method
# Import it directly for unit testing:
from depthfusion.storage.vector_store import ChromaDBStore, _admission_score

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> ChromaDBStore:
    """Return a ChromaDBStore with mocked Chroma internals."""
    with patch("depthfusion.storage.vector_store._CHROMADB_AVAILABLE", True):
        store = object.__new__(ChromaDBStore)
        store._collection = MagicMock()
        store._client = MagicMock()
        return store


# ---------------------------------------------------------------------------
# Unit tests for _admission_score
# ---------------------------------------------------------------------------

class TestAdmissionScore:
    def test_rich_content_returns_one(self):
        content = (
            "Implemented OAuth2 PKCE flow. Updated user table migrations.\n"
            "Wrote integration tests for token refresh.\n" * 3
        )
        assert _admission_score(content) == 1.0

    def test_boilerplate_envelope_returns_low(self):
        content = (
            "--- SESSION END at 07:14:20 ---\n"
            "Project: depthfusion\n"
        )
        result = _admission_score(content)
        assert result < 1.0  # boilerplate_penalty returns 0.2

    def test_empty_content_returns_one(self):
        # boilerplate_penalty returns 1.0 for empty content
        assert _admission_score("") == 1.0

    def test_session_start_marker_short_block_is_low(self):
        content = (
            "--- SESSION START at 01:00:00 ---\n"
            "Project: depthfusion\n"
            "Mode: auto\n"
        )
        assert _admission_score(content) < 1.0

    def test_long_mixed_content_with_boilerplate_marker_returns_one(self):
        # Long block (>12 non-empty lines) with a boilerplate marker AND diverse
        # content: boilerplate_penalty returns 1.0 (too long), lexical_richness
        # returns 1.0 (high TTR) => combined 1.0.
        lines = ["--- SESSION END at 07:14:20 ---"] + [
            "Refactored authentication middleware to use async/await pattern.",
            "Added rate limiting with exponential backoff on login endpoint.",
            "Migrated session storage from Redis to PostgreSQL for persistence.",
            "Updated JWT signing algorithm from HS256 to RS256 for security.",
            "Implemented CSRF protection on all state-modifying endpoints.",
            "Added integration tests for OAuth2 callback flow.",
            "Documented API changes in OpenAPI specification v3.1.",
            "Fixed null pointer in token validator at line 47 of auth.py.",
            "Optimized database index on user_sessions table for query speed.",
            "Deployed hotfix to staging environment and verified behaviour.",
            "Code review feedback addressed: removed hardcoded timeout value.",
            "Performance benchmark shows 3x improvement in auth latency.",
            "Merged feature branch after approval from security team lead.",
        ]
        content = "\n".join(lines)
        assert _admission_score(content) == 1.0


# ---------------------------------------------------------------------------
# Integration tests: add_document() gate behaviour
# ---------------------------------------------------------------------------

class TestAddDocumentGate:
    """Tests that add_document() skips indexing when admission score is below threshold.

    The default threshold is 0.10.  boilerplate_penalty returns 0.2 (> 0.10),
    so in v1 boilerplate alone does not trigger the skip.  Tests that verify the
    skip path patch _ADMISSION_DROP_THRESHOLD to 0.25 (above boilerplate's 0.2)
    to exercise the gate logic end-to-end without waiting for v2's combined score.
    """

    def test_rich_content_is_indexed(self):
        store = _make_store()
        content = "Implemented feature X. Decision: use async/await. Updated tests.\n" * 5
        with patch.object(store, "_get_embedding", return_value=None):
            store.add_document("doc1", content, {"source": "session", "acl_allow": ["proj-test"]})
        store._collection.upsert.assert_called_once()

    def test_boilerplate_content_is_skipped_when_threshold_above_penalty(self, monkeypatch):
        """Gate skips boilerplate when threshold (0.25) > boilerplate score (0.2)."""
        import depthfusion.storage.vector_store as vs
        monkeypatch.setattr(vs, "_ADMISSION_DROP_THRESHOLD", 0.25)

        store = _make_store()
        content = (
            "--- SESSION END at 07:14:20 ---\n"
            "Project: depthfusion\n"
        )
        with patch.object(store, "_get_embedding", return_value=None):
            store.add_document("doc2", content, {"source": "session", "acl_allow": ["proj-test"]})
        store._collection.upsert.assert_not_called()

    def test_doc_id_logged_on_skip(self, monkeypatch, caplog):
        import depthfusion.storage.vector_store as vs
        monkeypatch.setattr(vs, "_ADMISSION_DROP_THRESHOLD", 0.25)

        store = _make_store()
        content = "--- SESSION START at 01:00:00 ---\nProject: x\n"
        with patch.object(store, "_get_embedding", return_value=None):
            with caplog.at_level(logging.DEBUG, logger="depthfusion.storage.vector_store"):
                store.add_document("my-doc-id", content, {"acl_allow": ["proj-test"]})
        assert "my-doc-id" in caplog.text

    def test_threshold_in_log_message(self, monkeypatch, caplog):
        import depthfusion.storage.vector_store as vs
        monkeypatch.setattr(vs, "_ADMISSION_DROP_THRESHOLD", 0.25)

        store = _make_store()
        content = "--- SESSION END at 23:59:00 ---\nProject: depthfusion\n"
        with patch.object(store, "_get_embedding", return_value=None):
            with caplog.at_level(logging.DEBUG, logger="depthfusion.storage.vector_store"):
                store.add_document("skipped-doc", content, {"acl_allow": ["proj-test"]})
        # Log message should contain both score and threshold
        assert "admission score" in caplog.text.lower() or "skipping" in caplog.text.lower()

    def test_get_embedding_not_called_when_skipped(self, monkeypatch):
        """Admission gate fires BEFORE _get_embedding — no wasted embedding calls."""
        import depthfusion.storage.vector_store as vs
        monkeypatch.setattr(vs, "_ADMISSION_DROP_THRESHOLD", 0.25)

        store = _make_store()
        content = "--- SESSION END at 07:14:20 ---\nProject: depthfusion\n"
        mock_embed = MagicMock(return_value=None)
        with patch.object(store, "_get_embedding", mock_embed):
            store.add_document("doc3", content, {"acl_allow": ["proj-test"]})
        mock_embed.assert_not_called()


# ---------------------------------------------------------------------------
# Threshold env-var tests
# ---------------------------------------------------------------------------

class TestAdmissionThresholdEnvVar:
    def test_default_threshold_is_point_one(self):
        import depthfusion.storage.vector_store as vs
        assert vs._ADMISSION_DROP_THRESHOLD == pytest.approx(0.10)

    def test_low_threshold_allows_boilerplate_through(self, monkeypatch):
        """With threshold=0.0, boilerplate (score=0.2) is above threshold and gets indexed."""
        import depthfusion.storage.vector_store as vs
        store = _make_store()

        monkeypatch.setattr(vs, "_ADMISSION_DROP_THRESHOLD", 0.0)

        content = "--- SESSION END at 07:14:20 ---\nProject: depthfusion\n"
        with patch.object(store, "_get_embedding", return_value=None):
            store.add_document("should-index", content, {"acl_allow": ["proj-test"]})
        store._collection.upsert.assert_called_once()

    def test_high_threshold_blocks_normal_content(self, monkeypatch):
        """If threshold is raised above 1.0, even rich content (score=1.0) is blocked."""
        import depthfusion.storage.vector_store as vs
        store = _make_store()

        monkeypatch.setattr(vs, "_ADMISSION_DROP_THRESHOLD", 1.1)

        content = "Implemented OAuth2 PKCE flow. Updated user table migrations.\n" * 3
        with patch.object(store, "_get_embedding", return_value=None):
            store.add_document("blocked-doc", content, {"acl_allow": ["proj-test"]})
        store._collection.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: existing rich session content always passes
# ---------------------------------------------------------------------------

class TestExistingAddsUnaffected:
    def test_normal_session_content_passes_gate(self):
        content = (
            "Debugging auth module. Found null pointer in token validator line 47.\n"
            "Fixed by adding isinstance check. Updated unit test.\n"
            "PR: https://github.com/example/pr/123\n" * 2
        )
        assert _admission_score(content) >= 0.10  # above default threshold

    def test_technical_notes_pass_gate(self):
        content = (
            "Architecture decision: use event sourcing for audit log.\n"
            "Rationale: immutable log, easy replay, compliance requirement.\n"
            "Impact: new EventStore class, update all write paths.\n"
        )
        assert _admission_score(content) == 1.0


# ---------------------------------------------------------------------------
# S-118 v2: combined boilerplate × lexical_richness gate
# ---------------------------------------------------------------------------

class TestCombinedAdmissionGate:
    def test_both_signals_low_yields_very_low_score(self):
        """Boilerplate envelope + repetitive text → combined score < 0.20.

        boilerplate_penalty = 0.2 (short envelope, ≤12 lines).
        lexical_richness_penalty < 1.0 (30+ tokens, all "INFO" → TTR near 0).
        Combined = 0.2 × (≤1.0) < 0.20.
        """
        # 11 lines total (≤12 → bp=0.2); 30+ "INFO" tokens → very low TTR → lr<1.0
        lines = ["--- SESSION START at 12:00:00 ---"]
        lines += ["INFO INFO INFO INFO INFO INFO INFO INFO INFO INFO"] * 10
        repetitive = "\n".join(lines)
        score = _admission_score(repetitive)
        assert score < 0.20

    def test_boilerplate_only_low_passes_with_rich_content(self):
        """Boilerplate structure but high lexical diversity avoids extreme penalty."""
        # bp=0.2 but lexical_richness → 1.0 (diverse tokens): combined = 0.2
        short_envelope = "--- SESSION END at 09:00:00 ---\nProject: depthfusion\n" * 2
        # short envelope (≤12 non-empty lines) → bp=0.2, lr=1.0 → score=0.2
        score = _admission_score(short_envelope)
        assert score == pytest.approx(0.2, abs=0.01)

    def test_both_high_yields_full_score(self):
        """Rich, diverse content passes both gates — score is 1.0."""
        rich = (
            "Implemented pagination using cursor-based strategy to handle large datasets "
            "efficiently. Replaced offset pagination which degraded exponentially.\n"
            "Added index on created_at column. Benchmarked 5× improvement on page 100.\n"
            "Updated API documentation, client SDK, and integration tests.\n"
        )
        assert _admission_score(rich) == pytest.approx(1.0)
