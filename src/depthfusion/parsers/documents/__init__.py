"""depthfusion.parsers.documents — document parser protocol and registry.

Provides a MIME-type-keyed registry of :class:`DocumentParser` implementations
that normalise raw document bytes (PDF, DOCX, HTML, plain text, …) into
:class:`DocumentRecord` instances ready for chunking and embedding.

Public API::

    from depthfusion.parsers.documents import (
        DocumentParser,
        DocumentParserRegistry,
        DocumentRecord,
        QuarantineEntry,
        get_quarantine,
        quarantine,
    )
"""
from __future__ import annotations

from depthfusion.parsers.documents.base import (
    DocumentParser,
    DocumentParserRegistry,
    DocumentRecord,
    QuarantineEntry,
    get_quarantine,
    get_registry,
    quarantine,
)

__all__ = [
    "DocumentRecord",
    "DocumentParser",
    "DocumentParserRegistry",
    "QuarantineEntry",
    "get_quarantine",
    "get_registry",
    "quarantine",
]
