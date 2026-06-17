"""IngestPipeline — parse → chunk → embed → store (E-53).

The pipeline orchestrates the full ingestion flow for a single document:

1. **Hash check** (T-602) — If a :class:`FileMetadataIndex` is supplied,
   compare the document's SHA-256 against the stored ``content_hash``.
   Identical hash → no-op (return ``None``).  Changed / new hash →
   proceed and update the index.
2. **Parse** — :class:`~depthfusion.ingest.parser.DocumentParser` extracts
   plain text + metadata from the source file.  When
   ``DEPTHFUSION_OCR_ENABLED`` (canonical) or ``DEPTHFUSION_OCR`` (legacy
   alias) env var is set and the MIME type is an image (``image/png``,
   ``image/jpeg``), the OCR path is taken via
   :class:`~depthfusion.parsers.documents.ocr.OcrParser` instead (T-598).
   When the flag is off, image MIME types return ``None`` (skipped).
3. **Chunk** — A :class:`~depthfusion.ingest.chunking.ChunkingStrategy`
   splits the text into indexable chunks with ACL stamps inherited from
   the source record.
4. **Embed** — An optional embed callback writes chunks to a vector store.
   When no callback is provided the step is skipped (useful for tests).
5. **Store** — An optional store callback persists the
   :class:`~depthfusion.ingest.models.ParsedDocument`.  When no callback
   is provided the step is skipped.

Feature flags:
    ``DEPTHFUSION_OCR_ENABLED`` (canonical) or ``DEPTHFUSION_OCR`` (legacy):
        Set to ``1`` / ``true`` / ``yes`` to enable the OCR path for image
        MIME types.  When the flag is absent or ``0``, image documents are
        skipped (pipeline returns ``None`` for them).

Usage::

    from depthfusion.ingest import IngestPipeline

    pipeline = IngestPipeline()
    doc = pipeline.run("/path/to/report.docx")
    print(doc.chunks[:2])
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from depthfusion.ingest.chunking import ChunkingStrategy, FixedSizeChunker
from depthfusion.ingest.models import ParsedDocument
from depthfusion.ingest.parser import DocumentParser

if TYPE_CHECKING:
    from depthfusion.storage.file_index import FileMetadataIndex

# ---------------------------------------------------------------------------
# OCR feature-flag helper (T-598)
# ---------------------------------------------------------------------------

#: MIME types routed to OcrParser when the OCR flag is on.
_OCR_MIME_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg"})

#: Extension-to-MIME mapping for image types (for auto-detection in run()).
_IMAGE_EXT_TO_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _ocr_pipeline_enabled() -> bool:
    """Return True when the OCR feature flag is active.

    Checks the canonical ``DEPTHFUSION_OCR_ENABLED`` variable first;
    falls back to the legacy ``DEPTHFUSION_OCR`` alias for backward
    compatibility.
    """
    for var in ("DEPTHFUSION_OCR_ENABLED", "DEPTHFUSION_OCR"):
        raw = os.environ.get(var, "")
        if raw.strip() not in ("", "0", "false", "False", "no", "No"):
            return True
    return False


class IngestPipeline:
    """Orchestrates the parse → chunk → embed → store pipeline.

    Args:
        parser:             :class:`DocumentParser` instance.  A default
                            instance is created when not provided.
        chunker:            :class:`ChunkingStrategy` to use.  Defaults to
                            :class:`FixedSizeChunker` with 1000 tokens /
                            200 overlap.
        embed_callback:     Optional ``(doc: ParsedDocument) -> None``
                            called after chunking.  Intended for writing
                            to a vector store.
        store_callback:     Optional ``(doc: ParsedDocument) -> None``
                            called after embedding.  Intended for
                            persisting the record.
        file_index:         Optional :class:`~depthfusion.storage.file_index.FileMetadataIndex`.
                            When provided, :meth:`run` performs an atomic
                            replace-on-change check (T-602): if the
                            document's SHA-256 matches the stored
                            ``content_hash``, the pipeline is skipped
                            entirely and ``None`` is returned.  When the
                            hash differs (or no entry exists), the index is
                            updated and the pipeline proceeds normally.
    """

    def __init__(
        self,
        parser: DocumentParser | None = None,
        chunker: ChunkingStrategy | None = None,
        embed_callback: Callable[[ParsedDocument], None] | None = None,
        store_callback: Callable[[ParsedDocument], None] | None = None,
        file_index: "FileMetadataIndex | None" = None,
    ) -> None:
        self._parser = parser or DocumentParser()
        self._chunker = chunker or FixedSizeChunker(chunk_tokens=1000, overlap_tokens=200)
        self._embed = embed_callback
        self._store = store_callback
        self._file_index = file_index

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        path: str,
        mime_type: str | None = None,
        *,
        acl_allow: list[str] | None = None,
        classification: str | None = None,
    ) -> ParsedDocument | None:
        """Run the full ingestion pipeline for a single document.

        When a :class:`FileMetadataIndex` was supplied at construction time
        (T-602), this method reads the document bytes first, computes their
        SHA-256, and compares it with the stored ``content_hash``:

        * **Identical hash** → the document has not changed; skip all
          downstream steps (parse / chunk / embed / store) and return
          ``None``.
        * **Different hash (or no entry)** → update the index entry and
          proceed with the full pipeline.

        For image MIME types (``image/png``, ``image/jpeg``) the OCR path
        is taken only when ``DEPTHFUSION_OCR_ENABLED`` (or the legacy
        ``DEPTHFUSION_OCR``) env var is set (T-598).  When the flag is off
        the pipeline returns ``None`` for image documents (no-op).

        Without a ``file_index``, the method always proceeds (original
        behaviour).

        Args:
            path:           File-system path to the document.
            mime_type:      MIME type override (auto-detected from extension
                            if omitted).
            acl_allow:      Per-document ACL principal list.
            classification: Per-document classification label.

        Returns:
            The fully populated :class:`ParsedDocument` with ``chunks``
            filled in and ACL stamps inherited, or ``None`` when the
            document was unchanged and no re-ingestion was needed, or
            ``None`` when the OCR flag is off for an image document.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError:        If the file type is not supported.
        """
        # T-602 — Atomic replace-on-change via FileMetadataIndex.
        # Read raw bytes once to (a) compute the hash and (b) avoid a
        # redundant read inside the parser.  If the hash matches the stored
        # value, the document is identical to the last-ingested version and
        # we return None immediately without parsing, chunking, embedding,
        # or storing.
        raw_bytes_for_hash: bytes | None = None
        if self._file_index is not None:
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"Document not found: {path}")
            raw_bytes_for_hash = p.read_bytes()
            changed = self._file_index.upsert_with_hash(p, raw_bytes_for_hash)
            if not changed:
                # Identical content — no-op, skip all downstream steps.
                return None

        # Resolve the effective MIME type early so we can route to OCR.
        effective_mime = mime_type
        if effective_mime is None:
            suffix = Path(path).suffix.lower()
            effective_mime = _IMAGE_EXT_TO_MIME.get(suffix)

        # T-598 — OCR path for image MIME types.
        # When the flag is on, delegate to OcrParser and build a
        # ParsedDocument from the first DocumentRecord returned.
        # When the flag is off, return None (image skipped silently).
        if effective_mime in _OCR_MIME_TYPES:
            if not _ocr_pipeline_enabled():
                return None
            return self._run_ocr_path(
                path,
                effective_mime,
                acl_allow=acl_allow,
                classification=classification,
                raw_bytes=raw_bytes_for_hash,
            )

        # 1. Parse (standard non-image path)
        doc = self._parser.parse(
            path,
            mime_type,
            acl_allow=acl_allow,
            classification=classification,
        )

        # 2. Chunk — ACL stamps inherited automatically via the shared doc
        doc.chunks = self._chunker.chunk(doc.text)

        # 3. Embed (optional)
        if self._embed is not None:
            self._embed(doc)

        # 4. Store (optional)
        if self._store is not None:
            self._store(doc)

        return doc

    def run_from_bytes(
        self,
        source_id: str,
        data: bytes,
        mime_type: str,
        *,
        acl_allow: list[str] | None = None,
        classification: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ParsedDocument:
        """Run the pipeline on raw bytes without touching the file-system.

        This is the entry point used by connectors (e.g. the SharePoint
        connector) that download content directly into memory.

        Args:
            source_id:      Stable identifier for the document.
            data:           Raw document bytes.
            mime_type:      MIME type of the document.
            acl_allow:      Principal list for the record.
            classification: Classification label.
            metadata:       Additional key/value metadata to merge in.

        Returns:
            The populated :class:`ParsedDocument`.
        """
        import pathlib
        import tempfile

        ext_map = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "text/markdown": ".md",
            "image/png": ".png",
            "image/jpeg": ".jpg",
        }
        ext = ext_map.get(mime_type, ".bin")

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            result = self.run(
                tmp_path,
                mime_type=mime_type,
                acl_allow=acl_allow,
                classification=classification,
            )
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

        # run() returns None only when a FileMetadataIndex indicates the
        # content is unchanged, or when the OCR flag is off for an image.
        # run_from_bytes supplies raw bytes from a connector (no file_index
        # involved), so None is not expected here for non-image types.
        # Guard defensively so mypy and runtime agree.
        if result is None:
            raise RuntimeError(
                "IngestPipeline.run() returned None inside run_from_bytes; "
                "file_index should not be set when calling run_from_bytes, "
                "and OCR must be enabled for image MIME types."
            )

        # Override source_id and merge caller-supplied metadata
        result.source_id = source_id
        if metadata:
            result.metadata.update(metadata)

        return result

    # ------------------------------------------------------------------
    # OCR path helper (T-598)
    # ------------------------------------------------------------------

    def _run_ocr_path(
        self,
        path: str,
        mime_type: str,
        *,
        acl_allow: list[str] | None = None,
        classification: str | None = None,
        raw_bytes: bytes | None = None,
    ) -> ParsedDocument | None:
        """Delegate to OcrParser and convert the result to a ParsedDocument.

        Args:
            path:       File-system path to the image.
            mime_type:  Resolved image MIME type.
            acl_allow:  ACL principal list.
            classification: Classification label.
            raw_bytes:  Pre-read bytes (avoids a second disk read when the
                        file_index path already loaded them).

        Returns:
            A :class:`ParsedDocument` if OCR produced text, or ``None`` if
            OCR returned no text (blank scan, backend unavailable, etc.).
        """
        from depthfusion.parsers.documents.ocr import OcrParser

        p = Path(path)
        if raw_bytes is None:
            if not p.exists():
                raise FileNotFoundError(f"Document not found: {path}")
            raw_bytes = p.read_bytes()

        ocr_parser = OcrParser()
        records = ocr_parser.parse(str(p.resolve()), raw_bytes)
        if not records:
            return None

        record = records[0]
        text = record.content

        doc = ParsedDocument(
            source_id=str(p.resolve()),
            text=text,
            metadata={"title": record.title, "source_path": str(p)},
            acl_allow=acl_allow if acl_allow is not None else [],
            classification=classification or "internal",
            mime_type=mime_type,
            parse_timestamp=record.parse_timestamp,
        )

        # Chunk
        doc.chunks = self._chunker.chunk(text)

        # Embed (optional)
        if self._embed is not None:
            self._embed(doc)

        # Store (optional)
        if self._store is not None:
            self._store(doc)

        return doc


__all__ = ["IngestPipeline"]
