"""depthfusion.parsers.documents — document ingestion types and parsers.

Quarantine store (T-591)::

    from depthfusion.parsers.documents import (
        QuarantineEntry,
        QuarantineStore,
        quarantine,
        get_quarantine,
        get_quarantine_store,
    )

Document parsers (T-592)::

    from depthfusion.parsers.documents import GenericParser, get_registry

    parser = get_registry().get("text/markdown")
    records = parser.parse("readme.md", raw_bytes)
"""
from __future__ import annotations

from depthfusion.parsers.documents.base import (
    DocumentParser,
    DocumentRecord,
    QuarantineEntry,
    QuarantineStore,
    get_quarantine,
    get_quarantine_store,
    quarantine,
)
from depthfusion.parsers.documents.generic import GenericParser


class DocumentParserRegistry:
    """Registry mapping MIME types to DocumentParser instances."""

    def __init__(self) -> None:
        self._registry: dict[str, DocumentParser] = {}

    def register(self, parser: DocumentParser) -> None:
        """Register *parser* for each of its supported MIME types."""
        for mime_type in parser.supported_mime_types:
            self._registry[mime_type] = parser

    def get(self, mime_type: str) -> DocumentParser | None:
        """Return the parser registered for *mime_type*, or ``None``."""
        return self._registry.get(mime_type)

    def supported_types(self) -> list[str]:
        """Return all registered MIME types."""
        return list(self._registry.keys())

    def registered_types(self) -> list[str]:
        """Alias for supported_types() — return all registered MIME types."""
        return self.supported_types()


# Module-level default registry singleton
_default_registry = DocumentParserRegistry()
_default_registry.register(GenericParser())


def get_registry() -> DocumentParserRegistry:
    """Return the shared default DocumentParserRegistry."""
    return _default_registry


__all__ = [
    # Document protocol types
    "DocumentParser",
    "DocumentParserRegistry",
    "DocumentRecord",
    # Parsers
    "GenericParser",
    "get_registry",
    # Quarantine store
    "QuarantineEntry",
    "QuarantineStore",
    "get_quarantine",
    "get_quarantine_store",
    "quarantine",
]
