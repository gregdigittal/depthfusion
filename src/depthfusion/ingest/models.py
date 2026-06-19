"""Data models for the document ingestion framework (E-53)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """A text chunk produced by a ChunkingStrategy, carrying heading context.

    Attributes:
        text:         The chunk text content.
        heading_path: The heading breadcrumb from the source document (empty string
                      when the parser did not supply one).
    """

    text: str
    heading_path: str = ""


@dataclass
class ParsedDocument:
    """The normalised output of parsing a document.

    Attributes:
        source_id:       Stable unique identifier (e.g. file path or SharePoint item id).
        text:            Full extracted plain text.
        metadata:        Arbitrary key/value pairs from the source (title, author, etc.).
        acl_allow:       List of principal identifiers permitted to see this document.
        classification:  Security classification label; defaults to "internal".
        chunks:          Chunks produced by a :class:`ChunkingStrategy`.
        mime_type:       MIME type of the original document.
        parse_timestamp: ISO-8601 string when parsing occurred; empty if unavailable.
    """

    source_id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)
    acl_allow: list[str] = field(default_factory=list)
    classification: str = "internal"
    chunks: list[Chunk] = field(default_factory=list)
    mime_type: str = "text/plain"
    parse_timestamp: str = ""


__all__ = ["Chunk", "ParsedDocument"]
