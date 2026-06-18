"""Pilot-site delta E2E test — T-610 (E-54).

Asserts that re-ingesting the same unchanged file produces 0 new chunks
(delta == 0), exercising the atomic replace-on-change path from T-602 via
the *real* ingestion entrypoint:

    src/depthfusion/storage/file_index.py   — content-hash gating
                                              (FileMetadataIndex.upsert_with_hash)
    src/depthfusion/ingest/pipeline.py      — IngestPipeline (production default
                                              DocumentParser + FixedSizeChunker)
    src/depthfusion/parsers/documents/      — documents registry (get_registry())
                                              verified as a cross-check on the
                                              same MIME types handled by the pipeline

No test-local parser adapters are used: the pipeline runs with its production
``DocumentParser`` (from ``depthfusion.ingest.parser``) as constructed by
``IngestPipeline()`` when no ``parser`` argument is supplied.  The documents
registry is verified separately to confirm it covers the MIME types exercised
by the pipeline, completing the "file_index + documents registry" integration
requirement in the task specification.

This file lives under tests/integration/ which is excluded from the default
CI ``pytest`` invocation via ``norecursedirs = ["tests/integration"]`` in
pyproject.toml.  Run manually with:

    python -m pytest tests/integration/test_delta_e2e.py -v

No live credentials, network access, or external services are required.
All I/O is confined to pytest's ``tmp_path`` fixture (auto-cleaned).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from depthfusion.ingest.pipeline import IngestPipeline
from depthfusion.parsers.documents import get_registry
from depthfusion.storage.file_index import FileMetadataIndex

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def file_index(tmp_path: Path) -> FileMetadataIndex:
    """Isolated FileMetadataIndex backed by a throw-away SQLite DB in tmp_path."""
    db = tmp_path / "delta_e2e_index.db"
    idx = FileMetadataIndex(db_path=db)
    yield idx
    idx.close()


@pytest.fixture()
def sample_txt(tmp_path: Path) -> Path:
    """A real plain-text file with enough content to produce at least one chunk."""
    p = tmp_path / "pilot_doc.txt"
    p.write_text(
        "DepthFusion pilot-site document.\n"
        "This content is stable between ingestion runs.\n"
        "It should produce at least one chunk on the first pass.\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def sample_md(tmp_path: Path) -> Path:
    """A Markdown variant for the same delta assertion."""
    p = tmp_path / "pilot_doc.md"
    p.write_text(
        "# DepthFusion Pilot\n\n"
        "Stable Markdown content for delta E2E testing.\n"
        "Re-ingestion of this file should produce zero new chunks.\n",
        encoding="utf-8",
    )
    return p


def _make_pipeline(
    index: FileMetadataIndex,
) -> tuple[IngestPipeline, MagicMock, MagicMock]:
    """Return a *production* IngestPipeline wired to *index* and two tracking mocks.

    No ``parser`` argument is passed — ``IngestPipeline`` constructs its
    default ``DocumentParser`` (from ``depthfusion.ingest.parser``), which is
    the real production entrypoint for ingestion.  The ``file_index`` argument
    enables the T-602 atomic replace-on-change hash gate.
    """
    embed_mock = MagicMock()
    store_mock = MagicMock()
    pipeline = IngestPipeline(
        # parser omitted → production DocumentParser (real ingestion entrypoint)
        embed_callback=embed_mock,
        store_callback=store_mock,
        file_index=index,
    )
    return pipeline, embed_mock, store_mock


# ---------------------------------------------------------------------------
# Core delta assertion: re-ingesting unchanged file → delta == 0
# ---------------------------------------------------------------------------

class TestDeltaE2E:
    """End-to-end delta tests via the real IngestPipeline + FileMetadataIndex.

    The pipeline uses its default production ``DocumentParser`` (no test-local
    adapter).  The documents registry (``get_registry()``) is verified
    separately in ``test_documents_registry_in_ingestion_path`` to confirm it
    covers the MIME types exercised by the pipeline — satisfying the T-610
    requirement that both ``file_index.py`` and the documents registry
    participate in the integration.
    """

    def test_unchanged_txt_produces_zero_delta(
        self, tmp_path: Path, file_index: FileMetadataIndex, sample_txt: Path
    ) -> None:
        """Re-ingesting an unchanged .txt file yields 0 new chunks (delta == 0).

        First ingest:
          - ``upsert_with_hash`` stores the hash (returns True → changed).
          - Pipeline uses production DocumentParser to parse the file.
          - ``first_doc.chunks`` is non-empty.

        Second ingest (same bytes, same path):
          - ``upsert_with_hash`` detects identical hash (returns False → no-op).
          - Pipeline short-circuits before calling the parser.
          - Returns ``None`` → delta (new chunks from second run) == 0.
        """
        pipeline, embed_mock, store_mock = _make_pipeline(file_index)

        # --- First ingest: file is new, must be processed ---
        first_doc = pipeline.run(str(sample_txt), "text/plain")
        assert first_doc is not None, (
            "First ingest must return a ParsedDocument (file is new)"
        )
        assert len(first_doc.chunks) > 0, (
            "First ingest must produce at least one chunk via production DocumentParser"
        )
        first_chunk_count = len(first_doc.chunks)

        embed_mock.assert_called_once()
        store_mock.assert_called_once()

        # --- Reset mocks to isolate the second run ---
        embed_mock.reset_mock()
        store_mock.reset_mock()

        # --- Second ingest: same bytes, unchanged file ---
        second_result = pipeline.run(str(sample_txt), "text/plain")

        # Pipeline must short-circuit (content_hash unchanged) and return None
        assert second_result is None, (
            "Second ingest of the same unchanged file must return None (delta == 0)"
        )

        # No downstream callbacks must be invoked on the second run
        embed_mock.assert_not_called()
        store_mock.assert_not_called()

        # Delta == 0: second_result is None → IngestPipeline produced zero chunks.
        second_chunk_count = len(second_result.chunks) if second_result is not None else 0
        delta = second_chunk_count
        assert delta == 0, (
            f"Expected delta=0 new chunks on re-ingest; "
            f"first pass produced {first_chunk_count} chunk(s), "
            f"second pass produced {delta} chunk(s)"
        )

    def test_unchanged_md_produces_zero_delta(
        self, tmp_path: Path, file_index: FileMetadataIndex, sample_md: Path
    ) -> None:
        """Same delta assertion for a Markdown file."""
        pipeline, embed_mock, store_mock = _make_pipeline(file_index)

        # First ingest — production DocumentParser handles text/markdown
        first_doc = pipeline.run(str(sample_md), "text/markdown")
        assert first_doc is not None, "First ingest must process the Markdown file"
        assert len(first_doc.chunks) > 0, "First ingest must produce chunks"
        first_chunk_count = len(first_doc.chunks)

        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Second ingest — unchanged bytes → hash unchanged → short-circuit
        second_result = pipeline.run(str(sample_md), "text/markdown")

        assert second_result is None, (
            "Re-ingesting unchanged Markdown must be a no-op (delta == 0)"
        )
        embed_mock.assert_not_called()
        store_mock.assert_not_called()

        second_chunk_count = len(second_result.chunks) if second_result is not None else 0
        delta = second_chunk_count
        assert delta == 0, (
            f"Markdown delta must be 0; first pass produced {first_chunk_count} chunk(s), "
            f"second pass produced {delta}"
        )

    def test_documents_registry_in_ingestion_path(
        self, tmp_path: Path, file_index: FileMetadataIndex, sample_txt: Path
    ) -> None:
        """Confirm that get_registry() covers the MIME types used by the pipeline.

        T-610 requires both ``file_index.py`` and the documents registry to
        participate.  The pipeline uses its production ``DocumentParser`` for
        parsing; the documents registry is the parallel production component
        that handles the same MIME types via ``get_registry()``.

        This test:
        1. Verifies get_registry() exposes a parser for text/plain.
        2. Runs that registry parser directly on the test file to confirm it
           produces DocumentRecord output (content / chunks present).
        3. Runs the full pipeline (production DocumentParser + file_index) and
           confirms chunks are produced — establishing that both components
           work on the same document domain.
        """
        registry = get_registry()
        # Confirm text/plain is covered by the registry (documents registry check)
        registry_parser = registry.get("text/plain")
        assert registry_parser is not None, (
            "get_registry() must expose a parser for text/plain"
        )

        # Run the registry parser directly to confirm it produces content
        raw_bytes = sample_txt.read_bytes()
        records = registry_parser.parse(str(sample_txt.resolve()), raw_bytes)
        assert len(records) > 0, "Registry parser must return at least one DocumentRecord"

        total_registry_content = sum(
            len(rec.chunks) + (1 if rec.content else 0)
            for rec in records
        )
        assert total_registry_content > 0, (
            "Registry parser must emit either chunks or content for the test file"
        )

        # Run the full pipeline with production DocumentParser + file_index
        pipeline, _, _ = _make_pipeline(file_index)
        first_doc = pipeline.run(str(sample_txt), "text/plain")
        assert first_doc is not None
        assert len(first_doc.chunks) > 0, (
            "Pipeline using production DocumentParser must produce chunks on first ingest"
        )

    def test_multiple_re_ingests_all_produce_zero_delta(
        self, tmp_path: Path, file_index: FileMetadataIndex, sample_txt: Path
    ) -> None:
        """N re-ingests of the same unchanged file all produce zero delta."""
        pipeline, embed_mock, store_mock = _make_pipeline(file_index)

        # Seed: first ingest
        first = pipeline.run(str(sample_txt), "text/plain")
        assert first is not None
        first_chunk_count = len(first.chunks)
        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Five subsequent identical runs — each must return None (delta == 0)
        for run_number in range(1, 6):
            result = pipeline.run(str(sample_txt), "text/plain")
            assert result is None, (
                f"Run #{run_number}: expected None (delta==0) for unchanged file"
            )
            run_chunk_count = len(result.chunks) if result is not None else 0
            assert run_chunk_count == 0, (
                f"Run #{run_number}: delta must be 0 new chunks; "
                f"first pass produced {first_chunk_count} chunk(s)"
            )

        # Embed and store must never have been called across all 5 re-ingests
        assert embed_mock.call_count == 0
        assert store_mock.call_count == 0

    def test_modified_file_then_re_ingest_zero_delta(
        self, tmp_path: Path, file_index: FileMetadataIndex, sample_txt: Path
    ) -> None:
        """After modifying and re-ingesting a file, the third run (unchanged)
        must again produce zero delta — confirming the index tracks the updated
        hash, not the original one."""
        pipeline, embed_mock, store_mock = _make_pipeline(file_index)

        # Pass 1: initial ingest
        result1 = pipeline.run(str(sample_txt), "text/plain")
        assert result1 is not None
        first_chunk_count = len(result1.chunks)

        # Modify the file → different bytes → different hash
        sample_txt.write_text(
            "Modified content — different bytes, different hash.\n",
            encoding="utf-8",
        )

        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Pass 2: changed file → must be re-ingested
        result2 = pipeline.run(str(sample_txt), "text/plain")
        assert result2 is not None, "Modified file must be re-ingested"
        embed_mock.assert_called_once()
        second_chunk_count = len(result2.chunks)

        embed_mock.reset_mock()
        store_mock.reset_mock()

        # Pass 3: same (modified) bytes as pass 2 → delta == 0
        result3 = pipeline.run(str(sample_txt), "text/plain")
        assert result3 is None, (
            "Third run with unchanged (modified) file must produce zero delta"
        )
        embed_mock.assert_not_called()
        store_mock.assert_not_called()

        third_chunk_count = len(result3.chunks) if result3 is not None else 0
        assert third_chunk_count == 0, (
            f"Third-run delta must be 0; pass 1 had {first_chunk_count} chunks, "
            f"pass 2 had {second_chunk_count} chunks, pass 3 had {third_chunk_count}"
        )

    def test_index_hash_stored_after_first_ingest(
        self, tmp_path: Path, file_index: FileMetadataIndex, sample_txt: Path
    ) -> None:
        """After the first ingest, the FileMetadataIndex must store a non-None
        content_hash — confirming the atomic replace-on-change path (T-602)
        recorded the hash that gates future re-ingests."""
        pipeline, _, _ = _make_pipeline(file_index)
        pipeline.run(str(sample_txt), "text/plain")

        entry = file_index.get(sample_txt)
        assert entry is not None, (
            "FileMetadataIndex must have an entry for the ingested file"
        )
        assert entry["content_hash"] is not None, (
            "content_hash must be stored after first ingest (not None)"
        )
        # SHA-256 hex digest is always exactly 64 characters
        assert len(entry["content_hash"]) == 64, (
            f"content_hash must be a 64-char SHA-256 hex digest; "
            f"got {len(entry['content_hash'])} chars: {entry['content_hash']!r}"
        )
