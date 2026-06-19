"""depthfusion.ingest — Document Ingestion Framework (E-53).

Public API::

    from depthfusion.ingest import (
        Chunk,
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

from depthfusion.ingest.chunking import ChunkingStrategy, FixedSizeChunker, SentenceBoundaryChunker
from depthfusion.ingest.models import Chunk, ParsedDocument
from depthfusion.ingest.parser import DocumentParser
from depthfusion.ingest.pipeline import IngestPipeline

__all__ = [
    "Chunk",
    "ParsedDocument",
    "DocumentParser",
    "ChunkingStrategy",
    "FixedSizeChunker",
    "SentenceBoundaryChunker",
    "IngestPipeline",
]
