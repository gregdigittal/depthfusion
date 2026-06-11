"""Base types and quarantine infrastructure for document ingestion.

T-590: DocumentRecord dataclass, DocumentParser protocol, QuarantineEntry + registry.
T-591: QuarantineStore class with retry tracking semantics.

Usage::

    from depthfusion.parsers.documents.base import (
        QuarantineEntry,
        QuarantineStore,
        quarantine,
        get_quarantine,
        get_quarantine_store,
    )

    entry = QuarantineEntry(
        source_id="doc-123",
        error_message="Failed to decode UTF-8",
        timestamp="2026-06-11T10:00:00Z",
        raw_size_bytes=4096,
    )

    # Module-level convenience helpers (backward-compat):
    quarantine(entry)
    all_entries = get_quarantine()

    # Fine-grained store access:
    store = get_quarantine_store()
    retryable = store.list_retryable("2026-06-11T11:00:00Z")
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# DocumentRecord and DocumentParser (T-590)
# ---------------------------------------------------------------------------

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
    source_type: str = "file"  # "file" | "sharepoint" | "url"
    title: str = ""
    content: str = ""  # full extracted plain text
    chunks: list[str] = field(default_factory=list)  # paragraph chunks for embedding
    heading_path: list[str] = field(default_factory=list)  # breadcrumb of headings
    mime_type: str = "text/plain"
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
            source_id: Stable identifier for the source document.
            data:      Raw bytes of the document.

        Returns:
            A list of :class:`DocumentRecord` instances.  May be empty on
            failure, but should not raise.
        """
        ...


# ---------------------------------------------------------------------------
# QuarantineEntry and QuarantineStore (T-590 / T-591)
# ---------------------------------------------------------------------------

@dataclass
class QuarantineEntry:
    """Record of a document that failed to ingest and may be retried.

    Attributes:
        source_id:      Stable identifier for the document/source.
        error_message:  Human-readable description of the initial failure.
        timestamp:      ISO-8601 string when the entry was quarantined.
        raw_size_bytes: Size of the raw document payload, in bytes.
        retry_count:    How many retry attempts have been made so far.
        max_retries:    Maximum number of retry attempts allowed.
        next_retry_at:  ISO-8601 string for the next scheduled retry, or
                        empty string if no retry is scheduled (exhausted or
                        not yet set).
        last_error:     Error message from the most recent retry attempt.
    """

    source_id: str
    error_message: str
    timestamp: str  # ISO-8601
    raw_size_bytes: int

    # Retry-tracking fields (T-591)
    retry_count: int = 0
    max_retries: int = 3
    next_retry_at: str = ""  # ISO-8601 or "" if exhausted / not yet scheduled
    last_error: str = ""

    @property
    def is_exhausted(self) -> bool:
        """Return True when no further retries are permitted."""
        return self.retry_count >= self.max_retries


class QuarantineStore:
    """In-memory store for quarantined document entries with retry tracking.

    Entries are keyed by *source_id* — adding an entry with the same
    source_id as an existing entry replaces it (upsert semantics).
    """

    def __init__(self) -> None:
        self._entries: dict[str, QuarantineEntry] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------

    def add(self, entry: QuarantineEntry) -> None:
        """Add or update (upsert) a :class:`QuarantineEntry` by source_id."""
        with self._lock:
            self._entries[entry.source_id] = entry

    def remove(self, source_id: str) -> bool:
        """Remove the entry for *source_id*.

        Returns:
            ``True`` if an entry existed and was removed; ``False`` otherwise.
        """
        with self._lock:
            if source_id in self._entries:
                del self._entries[source_id]
                return True
            return False

    def record_retry_failure(
        self,
        source_id: str,
        error: str,
        next_retry_iso: str,
    ) -> None:
        """Record a failed retry attempt for the given *source_id*.

        Increments ``retry_count``, updates ``last_error``, and sets
        ``next_retry_at``.  If the entry is now exhausted (``retry_count >=
        max_retries`` after the increment), ``next_retry_at`` is cleared to
        the empty string.

        Silently does nothing if *source_id* is not found in the store.
        """
        with self._lock:
            entry = self._entries.get(source_id)
            if entry is None:
                return

            entry.retry_count += 1
            entry.last_error = error

            if entry.retry_count >= entry.max_retries:
                entry.next_retry_at = ""
            else:
                entry.next_retry_at = next_retry_iso

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get(self, source_id: str) -> QuarantineEntry | None:
        """Return the entry for *source_id*, or ``None`` if not present."""
        with self._lock:
            return self._entries.get(source_id)

    def list_all(self) -> list[QuarantineEntry]:
        """Return all entries (any state)."""
        with self._lock:
            return list(self._entries.values())

    def list_retryable(self, now_iso: str) -> list[QuarantineEntry]:
        """Return entries that are eligible for a retry at *now_iso*.

        An entry is retryable when **both** conditions hold:

        1. ``retry_count < max_retries`` (not exhausted), and
        2. ``next_retry_at == ""`` **or** ``next_retry_at <= now_iso``
           (the scheduled window has arrived).
        """
        with self._lock:
            result: list[QuarantineEntry] = []
            for entry in self._entries.values():
                if entry.retry_count >= entry.max_retries:
                    continue
                if entry.next_retry_at == "" or entry.next_retry_at <= now_iso:
                    result.append(entry)
            return result

    def exhausted(self) -> list[QuarantineEntry]:
        """Return entries where ``retry_count >= max_retries``."""
        with self._lock:
            return [e for e in self._entries.values() if e.retry_count >= e.max_retries]


# ---------------------------------------------------------------------------
# Module-level singleton and backward-compat helpers (T-590 public surface)
# ---------------------------------------------------------------------------

_default_quarantine_store: QuarantineStore = QuarantineStore()
# Backward-compat alias used in some tests
_quarantine_store = _default_quarantine_store


def get_quarantine_store() -> QuarantineStore:
    """Return the module-level default :class:`QuarantineStore` singleton."""
    return _default_quarantine_store


def quarantine(entry: QuarantineEntry) -> None:
    """Add *entry* to the default quarantine store.

    Backward-compatible convenience wrapper around
    ``get_quarantine_store().add(entry)``.
    """
    get_quarantine_store().add(entry)


def get_quarantine() -> list[QuarantineEntry]:
    """Return all entries from the default quarantine store.

    Backward-compatible convenience wrapper around
    ``get_quarantine_store().list_all()``.
    """
    return get_quarantine_store().list_all()


__all__ = [
    "DocumentParser",
    "DocumentRecord",
    "QuarantineEntry",
    "QuarantineStore",
    "_quarantine_store",
    "get_quarantine",
    "get_quarantine_store",
    "quarantine",
]
