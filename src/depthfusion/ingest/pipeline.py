"""IngestPipeline — parse → chunk → embed → store (E-53).

The pipeline orchestrates the full ingestion flow for a single document:

1. **Parse** — :class:`~depthfusion.ingest.parser.DocumentParser` extracts
   plain text + metadata from the source file.
2. **Chunk** — A :class:`~depthfusion.ingest.chunking.ChunkingStrategy`
   splits the text into indexable chunks with ACL stamps inherited from
   the source record.
3. **Embed** — An optional embed callback writes chunks to a vector store.
   When no callback is provided the step is skipped (useful for tests).
4. **Store** — An optional store callback persists the
   :class:`~depthfusion.ingest.models.ParsedDocument`.  When no callback
   is provided the step is skipped.

Usage::

    from depthfusion.ingest import IngestPipeline

    pipeline = IngestPipeline()
    doc = pipeline.run("/path/to/report.docx")
    print(doc.chunks[:2])
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from depthfusion.ingest.chunking import ChunkingStrategy, FixedSizeChunker
from depthfusion.ingest.models import ParsedDocument
from depthfusion.ingest.parser import DocumentParser

if TYPE_CHECKING:
    pass


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
    """

    def __init__(
        self,
        parser: DocumentParser | None = None,
        chunker: ChunkingStrategy | None = None,
        embed_callback: Callable[[ParsedDocument], None] | None = None,
        store_callback: Callable[[ParsedDocument], None] | None = None,
    ) -> None:
        self._parser = parser or DocumentParser()
        self._chunker = chunker or FixedSizeChunker(chunk_tokens=1000, overlap_tokens=200)
        self._embed = embed_callback
        self._store = store_callback

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
    ) -> ParsedDocument:
        """Run the full ingestion pipeline for a single document.

        Args:
            path:           File-system path to the document.
            mime_type:      MIME type override (auto-detected from extension
                            if omitted).
            acl_allow:      Per-document ACL principal list.
            classification: Per-document classification label.

        Returns:
            The fully populated :class:`ParsedDocument` with ``chunks``
            filled in and ACL stamps inherited.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError:        If the file type is not supported.
        """
        # 1. Parse
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
        }
        ext = ext_map.get(mime_type, ".bin")

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            doc = self.run(
                tmp_path,
                mime_type=mime_type,
                acl_allow=acl_allow,
                classification=classification,
            )
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

        # Override source_id and merge caller-supplied metadata
        doc.source_id = source_id
        if metadata:
            doc.metadata.update(metadata)

        return doc


__all__ = ["IngestPipeline"]
