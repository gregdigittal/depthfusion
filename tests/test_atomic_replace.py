"""Tests for T-602 — atomic replace-on-change via FileMetadataIndex.content_hash.

Covers two layers:

Unit layer (FileMetadataIndex helpers):
  AC-1: Calling upsert_with_hash() with the same data twice returns False on
        the second call (no-op — identical hash).
  AC-2: Calling upsert_with_hash() with changed data returns True (record updated).
  AC-3: content_hash_changed() returns True when no entry exists.
  AC-4: content_hash_changed() returns False when the stored hash matches.
  AC-5: content_hash_changed() returns True when the data differs.

Integration layer (IngestPipeline wired to FileMetadataIndex):
  AC-6: Ingesting the same file content twice → second call is a no-op
        (embed and store callbacks are NOT called; pipeline returns None).
  AC-7: Ingesting changed content → callbacks ARE called; pipeline returns a doc.
  AC-8: First-ever ingest (no prior entry) → always processes; callbacks called.
"""
from __future__ import annotations

import hashlib
import pathlib
from unittest.mock import MagicMock

import pytest

from depthfusion.ingest.pipeline import IngestPipeline
from depthfusion.storage.file_index import FileMetadataIndex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def index(tmp_path: pathlib.Path) -> FileMetadataIndex:
    """Fresh FileMetadataIndex backed by an in-tmp-dir SQLite database."""
    db = tmp_path / "test_index.db"
    idx = FileMetadataIndex(db_path=db)
    yield idx
    idx.close()


@pytest.fixture()
def doc_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """A real file on disk that the index can stat."""
    p = tmp_path / "doc.txt"
    p.write_bytes(b"initial content")
    return p


# ---------------------------------------------------------------------------
# content_hash_changed() tests
# ---------------------------------------------------------------------------

class TestContentHashChanged:
    """Unit tests for the content_hash_changed() helper method."""

    def test_returns_true_when_no_entry(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """Missing index entry → True (treat as changed / first ingest)."""
        assert index.content_hash_changed(doc_path, b"any data") is True

    def test_returns_false_when_hash_matches(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """After storing, same data → False (no-op)."""
        data = b"same content"
        doc_path.write_bytes(data)
        index.update(doc_path, compute_hash=True)

        assert index.content_hash_changed(doc_path, data) is False

    def test_returns_true_when_hash_differs(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """After storing, different data → True (changed)."""
        old_data = b"old content"
        new_data = b"new content"
        doc_path.write_bytes(old_data)
        index.update(doc_path, compute_hash=True)

        assert index.content_hash_changed(doc_path, new_data) is True

    def test_returns_true_when_stored_hash_is_null(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """Entry stored without compute_hash=True has NULL hash → True (treat as changed)."""
        index.update(doc_path, compute_hash=False)  # no hash computed

        stored = index.get(doc_path)
        assert stored is not None
        assert stored["content_hash"] is None

        assert index.content_hash_changed(doc_path, b"any data") is True


# ---------------------------------------------------------------------------
# upsert_with_hash() — same-hash (no-op) tests
# ---------------------------------------------------------------------------

class TestUpsertWithHashNoOp:
    """Same-hash path: upsert_with_hash() returns False and makes no changes."""

    def test_same_hash_returns_false(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """Second call with identical data → False (no-op)."""
        data = b"document content"
        doc_path.write_bytes(data)

        first = index.upsert_with_hash(doc_path, data)
        assert first is True  # first call always inserts

        second = index.upsert_with_hash(doc_path, data)
        assert second is False  # identical → no-op

    def test_same_hash_zero_db_changes(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """indexed_at should not change when data is unchanged."""
        import time

        data = b"stable document"
        doc_path.write_bytes(data)
        index.upsert_with_hash(doc_path, data)

        before = index.get(doc_path)
        assert before is not None
        indexed_at_before = before["indexed_at"]

        time.sleep(0.01)  # ensure clock advances
        changed = index.upsert_with_hash(doc_path, data)

        assert changed is False
        after = index.get(doc_path)
        assert after is not None
        assert after["indexed_at"] == indexed_at_before  # unchanged

    def test_same_hash_multiple_no_ops(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """N identical calls → only the first returns True; rest False."""
        data = b"repeated content"
        doc_path.write_bytes(data)

        results = [index.upsert_with_hash(doc_path, data) for _ in range(5)]
        assert results[0] is True
        assert all(r is False for r in results[1:])


# ---------------------------------------------------------------------------
# upsert_with_hash() — changed-hash (replace) tests
# ---------------------------------------------------------------------------

class TestUpsertWithHashReplace:
    """Changed-hash path: upsert_with_hash() returns True and updates the record."""

    def test_changed_hash_returns_true(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """Different data on second call → True (record replaced)."""
        old_data = b"version one"
        new_data = b"version two"
        doc_path.write_bytes(old_data)

        first = index.upsert_with_hash(doc_path, old_data)
        assert first is True

        doc_path.write_bytes(new_data)
        second = index.upsert_with_hash(doc_path, new_data)
        assert second is True

    def test_changed_hash_updates_stored_hash(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """After a replace, the stored content_hash reflects the new data."""
        old_data = b"old version"
        new_data = b"new version"
        doc_path.write_bytes(old_data)
        index.upsert_with_hash(doc_path, old_data)

        doc_path.write_bytes(new_data)
        index.upsert_with_hash(doc_path, new_data)

        stored = index.get(doc_path)
        assert stored is not None
        expected_hash = hashlib.sha256(new_data).hexdigest()
        assert stored["content_hash"] == expected_hash

    def test_changed_hash_subsequent_same_is_noop(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """After a replace, passing the same new data again is a no-op."""
        old_data = b"first"
        new_data = b"second"
        doc_path.write_bytes(old_data)
        index.upsert_with_hash(doc_path, old_data)

        doc_path.write_bytes(new_data)
        index.upsert_with_hash(doc_path, new_data)  # True

        # Same data again → no-op
        no_change = index.upsert_with_hash(doc_path, new_data)
        assert no_change is False

    def test_alternating_versions_tracked_correctly(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        """Alternating between two versions always returns True on a change."""
        v1 = b"version A"
        v2 = b"version B"
        doc_path.write_bytes(v1)

        r1 = index.upsert_with_hash(doc_path, v1)  # insert
        assert r1 is True

        doc_path.write_bytes(v2)
        r2 = index.upsert_with_hash(doc_path, v2)  # change → True
        assert r2 is True

        doc_path.write_bytes(v1)
        r3 = index.upsert_with_hash(doc_path, v1)  # back to v1 → True
        assert r3 is True

        r4 = index.upsert_with_hash(doc_path, v1)  # same as stored → False
        assert r4 is False


# ---------------------------------------------------------------------------
# First-ever ingest
# ---------------------------------------------------------------------------

class TestFirstIngest:
    """No prior entry: the first upsert always returns True."""

    def test_first_call_returns_true_and_stores_hash(
        self, index: FileMetadataIndex, doc_path: pathlib.Path
    ) -> None:
        data = b"brand new document"
        doc_path.write_bytes(data)

        result = index.upsert_with_hash(doc_path, data)
        assert result is True

        stored = index.get(doc_path)
        assert stored is not None
        assert stored["content_hash"] == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# IngestPipeline integration tests (T-602 wiring into the ingestion path)
#
# These tests verify that IngestPipeline.run() uses FileMetadataIndex to
# implement atomic replace-on-change: same hash → pipeline is skipped
# entirely (embed and store callbacks are NOT called, None is returned).
# ---------------------------------------------------------------------------

class TestIngestPipelineAtomicReplace:
    """Prove T-602 is wired into the actual ingestion path, not just helpers."""

    def _make_pipeline(
        self, tmp_path: pathlib.Path, index: FileMetadataIndex
    ) -> tuple[IngestPipeline, MagicMock, MagicMock]:
        """Return a pipeline, embed mock, and store mock."""
        embed_mock = MagicMock()
        store_mock = MagicMock()
        pipeline = IngestPipeline(
            embed_callback=embed_mock,
            store_callback=store_mock,
            file_index=index,
        )
        return pipeline, embed_mock, store_mock

    def test_first_ingest_calls_callbacks(
        self, tmp_path: pathlib.Path, index: FileMetadataIndex
    ) -> None:
        """AC-8: First-ever ingest → embed and store callbacks are called."""
        doc = tmp_path / "first.txt"
        doc.write_bytes(b"Hello world, this is the document.")

        pipeline, embed_mock, store_mock = self._make_pipeline(tmp_path, index)
        result = pipeline.run(str(doc), "text/plain")

        assert result is not None, "First ingest should return a ParsedDocument"
        embed_mock.assert_called_once()
        store_mock.assert_called_once()

    def test_same_hash_is_noop_in_pipeline(
        self, tmp_path: pathlib.Path, index: FileMetadataIndex
    ) -> None:
        """AC-6: Same content ingested twice → second call returns None, callbacks not called."""
        content = b"Stable document content that does not change."
        doc = tmp_path / "stable.txt"
        doc.write_bytes(content)

        pipeline, embed_mock, store_mock = self._make_pipeline(tmp_path, index)

        # First ingest: processes normally
        first_result = pipeline.run(str(doc), "text/plain")
        assert first_result is not None

        # Reset mocks to track only the second call
        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Second ingest of the same content: should be a no-op
        second_result = pipeline.run(str(doc), "text/plain")

        assert second_result is None, (
            "Second run with identical content should return None (no-op)"
        )
        embed_mock.assert_not_called()
        store_mock.assert_not_called()

    def test_changed_hash_triggers_re_ingest(
        self, tmp_path: pathlib.Path, index: FileMetadataIndex
    ) -> None:
        """AC-7: Changed content → pipeline processes again, callbacks called."""
        doc = tmp_path / "changing.txt"
        doc.write_bytes(b"Version one of this document.")

        pipeline, embed_mock, store_mock = self._make_pipeline(tmp_path, index)

        # First ingest
        first_result = pipeline.run(str(doc), "text/plain")
        assert first_result is not None

        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Update content on disk → changed hash
        doc.write_bytes(b"Version two of this document - different content.")

        second_result = pipeline.run(str(doc), "text/plain")

        assert second_result is not None, (
            "Changed content should return a new ParsedDocument"
        )
        embed_mock.assert_called_once()
        store_mock.assert_called_once()

    def test_same_hash_zero_embed_store_calls(
        self, tmp_path: pathlib.Path, index: FileMetadataIndex
    ) -> None:
        """AC-6 (strict): embed/store call counts are exactly 0 on the no-op run."""
        doc = tmp_path / "zero_calls.txt"
        doc.write_bytes(b"No-op test document content here.")

        pipeline, embed_mock, store_mock = self._make_pipeline(tmp_path, index)

        # Seed the index
        pipeline.run(str(doc), "text/plain")

        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Run again — must be strict no-op
        pipeline.run(str(doc), "text/plain")

        assert embed_mock.call_count == 0
        assert store_mock.call_count == 0

    def test_multiple_same_hash_runs_all_noops(
        self, tmp_path: pathlib.Path, index: FileMetadataIndex
    ) -> None:
        """AC-6 (repeated): N identical runs after first ingest → N no-ops."""
        doc = tmp_path / "repeated.txt"
        doc.write_bytes(b"Repeated ingestion test - content is stable.")

        pipeline, embed_mock, store_mock = self._make_pipeline(tmp_path, index)

        # First ingest
        pipeline.run(str(doc), "text/plain")
        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Five subsequent identical runs
        for _ in range(5):
            result = pipeline.run(str(doc), "text/plain")
            assert result is None

        assert embed_mock.call_count == 0
        assert store_mock.call_count == 0

    def test_no_file_index_always_processes(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Without a file_index, every run always processes (original behaviour)."""
        doc = tmp_path / "no_index.txt"
        doc.write_bytes(b"Content for pipeline without index.")

        embed_mock = MagicMock()
        store_mock = MagicMock()
        pipeline = IngestPipeline(
            embed_callback=embed_mock,
            store_callback=store_mock,
            # no file_index
        )

        result1 = pipeline.run(str(doc), "text/plain")
        result2 = pipeline.run(str(doc), "text/plain")

        assert result1 is not None
        assert result2 is not None
        assert embed_mock.call_count == 2
        assert store_mock.call_count == 2
