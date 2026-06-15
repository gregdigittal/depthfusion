"""Tests for T-599 — parse budget and oversized-doc quarantine.

Verifies:
  AC-1: A document whose byte size exceeds the budget is quarantined, not parsed.
  AC-2: A document within the budget is parsed normally.
  AC-3: The quarantine entry captures the correct source_id and reason string.
  AC-4: Setting max_bytes=0 disables the budget (no limit).
"""
from __future__ import annotations

import os
import pathlib

import pytest

from depthfusion.ingest.parser import DocumentParser, _DEFAULT_PARSE_MAX_BYTES
from depthfusion.parsers.documents.base import QuarantineStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_txt(tmp_path: pathlib.Path, name: str, content: bytes) -> pathlib.Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParseBudgetQuarantine:
    """Oversized documents are routed to QuarantineStore, not parsed."""

    def test_oversized_doc_is_quarantined(self, tmp_path: pathlib.Path) -> None:
        """A doc whose byte size > max_bytes lands in the quarantine store."""
        budget = 100  # very small budget
        store = QuarantineStore()
        parser = DocumentParser(max_bytes=budget, quarantine_store=store)

        # Create a file larger than the budget
        content = b"A" * (budget + 1)
        doc_path = _write_txt(tmp_path, "big.txt", content)

        with pytest.raises(ValueError, match="parse budget"):
            parser.parse(str(doc_path), "text/plain")

        entries = store.list_all()
        assert len(entries) == 1
        entry = entries[0]
        assert str(doc_path.resolve()) == entry.source_id
        assert entry.raw_size_bytes == len(content)
        assert "parse budget" in entry.error_message.lower()

    def test_oversized_doc_does_not_return_parsed_document(
        self, tmp_path: pathlib.Path
    ) -> None:
        """parse() raises ValueError for oversized docs (no ParsedDocument returned)."""
        budget = 50
        store = QuarantineStore()
        parser = DocumentParser(max_bytes=budget, quarantine_store=store)
        doc_path = _write_txt(tmp_path, "toobig.txt", b"X" * (budget + 10))

        with pytest.raises(ValueError):
            parser.parse(str(doc_path), "text/plain")

    def test_quarantine_entry_reason_mentions_sizes(self, tmp_path: pathlib.Path) -> None:
        """The quarantine error message references both the doc size and budget."""
        budget = 200
        store = QuarantineStore()
        parser = DocumentParser(max_bytes=budget, quarantine_store=store)
        content = b"B" * (budget + 50)
        doc_path = _write_txt(tmp_path, "sized.txt", content)

        with pytest.raises(ValueError):
            parser.parse(str(doc_path), "text/plain")

        entry = store.list_all()[0]
        # Both the actual size and the budget limit should appear in the message.
        assert str(len(content)) in entry.error_message
        assert str(budget) in entry.error_message

    def test_uses_default_quarantine_store_when_none_given(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Without an explicit store, the module-level default store is used."""
        from depthfusion.parsers.documents.base import get_quarantine_store

        budget = 10
        parser = DocumentParser(max_bytes=budget)  # no quarantine_store
        doc_path = _write_txt(tmp_path, "default_qs.txt", b"Z" * (budget + 1))

        before = len(get_quarantine_store().list_all())
        with pytest.raises(ValueError):
            parser.parse(str(doc_path), "text/plain")

        after = len(get_quarantine_store().list_all())
        assert after == before + 1


class TestParseBudgetUnderLimit:
    """Documents within the budget are parsed normally."""

    def test_under_budget_doc_is_parsed(self, tmp_path: pathlib.Path) -> None:
        """A file smaller than max_bytes is parsed and returns a ParsedDocument."""
        budget = 1024
        store = QuarantineStore()
        parser = DocumentParser(max_bytes=budget, quarantine_store=store)

        content = b"Hello, world!"
        assert len(content) < budget
        doc_path = _write_txt(tmp_path, "small.txt", content)

        doc = parser.parse(str(doc_path), "text/plain")

        assert doc is not None
        assert "Hello" in doc.text
        assert store.list_all() == []  # nothing quarantined

    def test_exactly_at_budget_is_parsed(self, tmp_path: pathlib.Path) -> None:
        """A file exactly equal to max_bytes is NOT quarantined (> not >=)."""
        budget = 64
        store = QuarantineStore()
        parser = DocumentParser(max_bytes=budget, quarantine_store=store)

        content = b"C" * budget
        assert len(content) == budget
        doc_path = _write_txt(tmp_path, "exact.txt", content)

        doc = parser.parse(str(doc_path), "text/plain")
        assert doc is not None
        assert store.list_all() == []

    def test_budget_zero_disables_limit(self, tmp_path: pathlib.Path) -> None:
        """max_bytes=0 disables the budget — even very large files are parsed."""
        store = QuarantineStore()
        parser = DocumentParser(max_bytes=0, quarantine_store=store)

        # Simulate a file that would exceed any normal budget
        content = b"D" * 200
        doc_path = _write_txt(tmp_path, "unlimited.txt", content)

        doc = parser.parse(str(doc_path), "text/plain")
        assert doc is not None
        assert store.list_all() == []


class TestParseBudgetEnvVar:
    """DEPTHFUSION_PARSE_MAX_BYTES env var controls the default budget."""

    def test_env_var_sets_budget(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """Setting the env var to a small value quarantines an oversized doc."""
        monkeypatch.setenv("DEPTHFUSION_PARSE_MAX_BYTES", "20")
        store = QuarantineStore()
        # max_bytes=None → reads from env
        parser = DocumentParser(max_bytes=None, quarantine_store=store)
        assert parser._max_bytes == 20

        doc_path = _write_txt(tmp_path, "envtest.txt", b"E" * 30)
        with pytest.raises(ValueError, match="parse budget"):
            parser.parse(str(doc_path), "text/plain")

        assert len(store.list_all()) == 1

    def test_env_var_zero_disables_limit(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """DEPTHFUSION_PARSE_MAX_BYTES=0 disables the budget via env."""
        monkeypatch.setenv("DEPTHFUSION_PARSE_MAX_BYTES", "0")
        store = QuarantineStore()
        parser = DocumentParser(max_bytes=None, quarantine_store=store)
        assert parser._max_bytes == 0

        doc_path = _write_txt(tmp_path, "nolimit_env.txt", b"F" * 500)
        doc = parser.parse(str(doc_path), "text/plain")
        assert doc is not None
        assert store.list_all() == []
