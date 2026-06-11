"""Base types for document parsers. Mirrors parsers/base.py ConversationParser ergonomics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "DocumentRecord",
    "DocumentParser",
    "DocumentParserRegistry",
    "QuarantineEntry",
    "quarantine",
    "get_quarantine",
]


@dataclass
class DocumentRecord:
    """A single parsed document.

    Attributes:
        source_id:       Unique identifier for the source document.
        source_type:     One of "file", "sharepoint", or "url".
        title:           Document title or filename.
        content:         Full extracted plain text.
        chunks:          Paragraph chunks for embedding.
        heading_path:    Breadcrumb of headings above the chunk.
        mime_type:       MIME type of the original document.
        acl_allow:       List of principal identifiers allowed to view.
        classification:  Security classification; defaults to "internal".
        parse_timestamp: ISO-8601 timestamp when parsing occurred; empty if unavailable.
    """

    source_id: str
    source_type: str  # "file" | "sharepoint" | "url"
    title: str
    content: str  # full extracted plain text
    chunks: list[str]  # paragraph chunks for embedding
    heading_path: list[str]  # breadcrumb of headings above chunk
    mime_type: str
    acl_allow: list[str] = field(default_factory=list)
    classification: str = "internal"
    parse_timestamp: str = ""


@runtime_checkable
class DocumentParser(Protocol):
    """Provider-agnostic document parser contract.

    Implementations MUST:
      - Return an empty list on empty input or malformed data.
      - Never raise; swallow parse errors and return what could be decoded.
      - Populate ``chunks`` and ``heading_path`` as best-effort from the
        document structure; empty lists are acceptable if not applicable.
    """

    name: str
    supported_mime_types: list[str]

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        """Parse raw document bytes into a list of DocumentRecords.

        Args:
            source_id: Unique identifier for the source (e.g. file path, URL).
            data:      Raw document bytes.

        Returns:
            A list of :class:`DocumentRecord` instances; may be empty.
        """
        ...


class DocumentParserRegistry:
    """Registry mapping MIME types to :class:`DocumentParser` implementations."""

    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}

    def register(self, parser: DocumentParser) -> None:
        """Register *parser* for each of its supported MIME types."""
        for mime in parser.supported_mime_types:
            self._parsers[mime] = parser

    def get(self, mime_type: str) -> DocumentParser | None:
        """Return the parser registered for *mime_type*, or ``None``."""
        return self._parsers.get(mime_type)

    def registered_types(self) -> list[str]:
        """Return all MIME types that have a registered parser."""
        return list(self._parsers.keys())


_default_registry = DocumentParserRegistry()


def get_registry() -> DocumentParserRegistry:
    """Return the module-level default :class:`DocumentParserRegistry`."""
    return _default_registry


@dataclass
class QuarantineEntry:
    """A record of a document that failed to parse.

    Attributes:
        source_id:       Identifier of the document that failed.
        error_message:   Human-readable description of the failure.
        timestamp:       ISO-8601 string when the failure occurred.
        raw_size_bytes:  Size of the raw input in bytes.
    """

    source_id: str
    error_message: str
    timestamp: str
    raw_size_bytes: int


_quarantine_store: list[QuarantineEntry] = []


def quarantine(entry: QuarantineEntry) -> None:
    """Add *entry* to the module-level quarantine store."""
    _quarantine_store.append(entry)


def get_quarantine() -> list[QuarantineEntry]:
    """Return a snapshot of the current quarantine store."""
    return list(_quarantine_store)
