"""Tests for the DocumentParser protocol and registry (T-590 / S-169 AC-1)."""
from __future__ import annotations

import pytest

from depthfusion.parsers.documents import (
    DocumentParser,
    DocumentParserRegistry,
    DocumentRecord,
    QuarantineEntry,
    get_quarantine,
    quarantine,
)
from depthfusion.parsers.documents.base import _quarantine_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_record(source_id: str = "doc-001") -> DocumentRecord:
    return DocumentRecord(
        source_id=source_id,
        source_type="file",
        title="Test Doc",
        content="Hello world",
        chunks=["Hello world"],
        heading_path=["Section 1"],
        mime_type="text/plain",
    )


class _FakeParser:
    """Minimal concrete class satisfying the DocumentParser Protocol."""

    name = "fake"
    supported_mime_types = ["text/plain"]

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        return [_make_minimal_record(source_id)]


class _MultiMimeParser:
    """Parser that claims two MIME types."""

    name = "multi"
    supported_mime_types = ["application/pdf", "application/msword"]

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_document_record_defaults() -> None:
    """DocumentRecord.classification defaults to 'internal'."""
    record = DocumentRecord(
        source_id="x",
        source_type="url",
        title="T",
        content="",
        chunks=[],
        heading_path=[],
        mime_type="text/html",
    )
    assert record.classification == "internal"
    assert record.parse_timestamp == ""
    assert record.acl_allow == []


def test_registry_register_and_get() -> None:
    """register() then get() by the same MIME type returns the parser."""
    registry = DocumentParserRegistry()
    parser = _FakeParser()
    registry.register(parser)
    result = registry.get("text/plain")
    assert result is parser


def test_registry_unknown_mime_returns_none() -> None:
    """get() with an unregistered MIME type returns None."""
    registry = DocumentParserRegistry()
    assert registry.get("image/png") is None


def test_registry_registers_all_mime_types() -> None:
    """A parser with two MIME types is accessible by both."""
    registry = DocumentParserRegistry()
    parser = _MultiMimeParser()
    registry.register(parser)
    assert registry.get("application/pdf") is parser
    assert registry.get("application/msword") is parser


def test_quarantine_entry_stored() -> None:
    """quarantine() adds the entry and get_quarantine() returns it."""
    # Use a fresh store snapshot length to avoid test-order sensitivity
    before = len(get_quarantine())

    entry = QuarantineEntry(
        source_id="bad-doc",
        error_message="Corrupt PDF header",
        timestamp="2026-06-10T12:00:00Z",
        raw_size_bytes=1024,
    )
    quarantine(entry)

    after = get_quarantine()
    assert len(after) == before + 1
    assert after[-1] is entry


def test_document_parser_protocol_structural_check() -> None:
    """A class with the correct interface satisfies isinstance(x, DocumentParser)."""
    parser = _FakeParser()
    assert isinstance(parser, DocumentParser)


def test_document_parser_protocol_missing_method_fails() -> None:
    """A class missing parse() does NOT satisfy the Protocol."""

    class _Incomplete:
        name = "incomplete"
        supported_mime_types = ["text/plain"]
        # deliberately omits parse()

    obj = _Incomplete()
    assert not isinstance(obj, DocumentParser)


def test_registry_registered_types_lists_all() -> None:
    """registered_types() returns every MIME type that has been registered."""
    registry = DocumentParserRegistry()
    registry.register(_FakeParser())
    registry.register(_MultiMimeParser())
    types = registry.registered_types()
    assert "text/plain" in types
    assert "application/pdf" in types
    assert "application/msword" in types


def test_document_record_round_trip_via_parser() -> None:
    """Calling parse() on a concrete parser returns DocumentRecord instances."""
    parser = _FakeParser()
    records = parser.parse("src-99", b"irrelevant bytes")
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, DocumentRecord)
    assert record.source_id == "src-99"
    assert record.mime_type == "text/plain"
