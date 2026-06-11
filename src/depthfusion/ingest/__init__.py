"""depthfusion.ingest — Document Ingestion Framework (E-53).

Public API::

    from depthfusion.ingest import (
        ParsedDocument,
        DocumentParser,
        ChunkingStrategy,
        FixedSizeChunker,
        SentenceBoundaryChunker,
        IngestPipeline,
    )

    pipeline = IngestPipeline()
    docs = pipeline.run("/path/to/file.docx")
"""
from __future__ import annotations

from depthfusion.ingest.models import ParsedDocument
from depthfusion.ingest.parser import DocumentParser
from depthfusion.ingest.chunking import ChunkingStrategy, FixedSizeChunker, SentenceBoundaryChunker
from depthfusion.ingest.pipeline import IngestPipeline

__all__ = [
    "ParsedDocument",
    "DocumentParser",
    "ChunkingStrategy",
    "FixedSizeChunker",
    "SentenceBoundaryChunker",
    "IngestPipeline",
]
