"""depthfusion.parsers.documents — document ingestion types and quarantine store.

Public API::

    from depthfusion.parsers.documents import (
        QuarantineEntry,
        QuarantineStore,
        quarantine,
        get_quarantine,
        get_quarantine_store,
    )
"""
from __future__ import annotations

from depthfusion.parsers.documents.base import (
    QuarantineEntry,
    QuarantineStore,
    get_quarantine,
    get_quarantine_store,
    quarantine,
)

__all__ = [
    "QuarantineEntry",
    "QuarantineStore",
    "get_quarantine",
    "get_quarantine_store",
    "quarantine",
]
