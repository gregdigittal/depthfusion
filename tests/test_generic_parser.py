"""Tests for depthfusion.parsers.documents.GenericParser (T-592 / S-169 AC-2).

Covers 9 required test cases:
  1. parse_plain_text — 2 paragraphs -> 1 record, 2 chunks
  2. parse_markdown_extracts_title — "# My Doc" -> title="My Doc"
  3. parse_markdown_heading_path — headings in heading_path
  4. parse_html_strips_tags — "<p>Hello</p>" -> no ``<`` in content
  5. parse_long_text_creates_multiple_chunks — 5000+ chars -> multiple chunks
  6. parse_empty_bytes — b"" -> no crash
  7. parse_latin1_fallback — invalid UTF-8 bytes -> no UnicodeDecodeError
  8. generic_parser_registered — get_registry().get("text/plain") returns GenericParser
  9. all_mime_types_registered — all 4 MIME types in registry
"""
from __future__ import annotations

import pytest

from depthfusion.parsers.documents import GenericParser, get_registry
from depthfusion.parsers.documents.base import DocumentRecord


@pytest.fixture()
def parser() -> GenericParser:
    return GenericParser()


# ──────────────────────────────────────────────────────────
# 1. Plain text — two paragraphs produce 1 record with 2 chunks
# ──────────────────────────────────────────────────────────

class TestPlainText:
    def test_parse_plain_text(self, parser: GenericParser) -> None:
        data = b"First paragraph here.\n\nSecond paragraph here."
        records = parser.parse("test.txt", data)
        assert len(records) == 1
        record = records[0]
        assert isinstance(record, DocumentRecord)
        assert len(record.chunks) == 2
        assert "First paragraph" in record.chunks[0]
        assert "Second paragraph" in record.chunks[1]


# ──────────────────────────────────────────────────────────
# 2. Markdown title extraction from first heading
# ──────────────────────────────────────────────────────────

class TestMarkdownTitle:
    def test_parse_markdown_extracts_title(self, parser: GenericParser) -> None:
        data = b"# My Doc\n\nSome content here."
        records = parser.parse("doc.md", data)
        assert records[0].title == "My Doc"

    def test_title_from_first_heading_not_text(self, parser: GenericParser) -> None:
        data = b"Preamble line\n\n# The Real Title\n\nBody."
        records = parser.parse("doc.md", data)
        # heading_re searches the whole doc; first match is "The Real Title"
        assert records[0].title == "The Real Title"

    def test_title_fallback_to_first_line_when_no_heading(self, parser: GenericParser) -> None:
        data = b"This is the first line\n\nSome body."
        records = parser.parse("doc.txt", data)
        assert records[0].title == "This is the first line"


# ──────────────────────────────────────────────────────────
# 3. Markdown heading path
# ──────────────────────────────────────────────────────────

class TestMarkdownHeadingPath:
    def test_parse_markdown_heading_path(self, parser: GenericParser) -> None:
        data = b"# Title\n\n## Section One\n\n### Subsection\n\n## Section Two\n"
        records = parser.parse("doc.md", data)
        heading_path = records[0].heading_path
        assert "Title" in heading_path
        assert "Section One" in heading_path
        assert "Subsection" in heading_path
        assert "Section Two" in heading_path
        assert heading_path.index("Title") < heading_path.index("Section One")

    def test_plain_text_has_empty_heading_path(self, parser: GenericParser) -> None:
        data = b"Just plain text, no headings."
        records = parser.parse("plain.txt", data)
        assert records[0].heading_path == []


# ──────────────────────────────────────────────────────────
# 4. HTML tag stripping
# ──────────────────────────────────────────────────────────

class TestHtmlStripping:
    def test_parse_html_strips_tags(self, parser: GenericParser) -> None:
        data = b"<html><body><p>Hello</p><p>World</p></body></html>"
        records = parser.parse("page.html", data)
        content = records[0].content
        assert "<" not in content
        assert "Hello" in content
        assert "World" in content

    def test_xhtml_strips_tags(self, parser: GenericParser) -> None:
        data = b'<?xml version="1.0"?><html><body><p>Test</p></body></html>'
        records = parser.parse("page.xhtml", data)
        assert "<" not in records[0].content
        assert "Test" in records[0].content

    def test_html_with_inline_tags(self, parser: GenericParser) -> None:
        data = b"<p>Hello <strong>world</strong> today</p>"
        records = parser.parse("fragment.html", data)
        content = records[0].content
        assert "<" not in content
        assert "Hello" in content
        assert "world" in content


# ──────────────────────────────────────────────────────────
# 5. Long text creates multiple chunks
# ──────────────────────────────────────────────────────────

class TestChunking:
    def test_parse_long_text_creates_multiple_chunks(self, parser: GenericParser) -> None:
        # Build 5 paragraphs of ~1000 chars each (total > 5000 chars)
        para = "A" * 900 + ". " + "B" * 900 + "."
        data = ("\n\n".join([para] * 5)).encode("utf-8")
        records = parser.parse("long.txt", data)
        assert len(records[0].chunks) > 1

    def test_short_text_single_chunk(self, parser: GenericParser) -> None:
        data = b"A short document."
        records = parser.parse("short.txt", data)
        assert len(records[0].chunks) == 1


# ──────────────────────────────────────────────────────────
# 6. Empty input does not crash
# ──────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_parse_empty_bytes(self, parser: GenericParser) -> None:
        records = parser.parse("empty.txt", b"")
        # Must not raise; returns 1 record with empty content
        assert len(records) == 1
        assert records[0].content == ""
        assert records[0].chunks == []

    def test_parse_none_data_does_not_crash(self, parser: GenericParser) -> None:
        """T-592 None guard: passing None instead of bytes must not raise."""
        result = parser.parse("none.txt", None)  # type: ignore[arg-type]
        assert isinstance(result, list)

    def test_source_id_preserved(self, parser: GenericParser) -> None:
        records = parser.parse("my/path/file.txt", b"Content here.")
        assert records[0].source_id == "my/path/file.txt"

    # ──────────────────────────────────────────────────────────
    # 7. Latin-1 fallback for invalid UTF-8
    # ──────────────────────────────────────────────────────────
    def test_parse_latin1_fallback(self, parser: GenericParser) -> None:
        # Byte sequence invalid as UTF-8 but valid latin-1.
        data = b"Caf\xe9 au lait"
        try:
            records = parser.parse("latin.txt", data)
        except UnicodeDecodeError:
            pytest.fail("GenericParser raised UnicodeDecodeError on invalid UTF-8 input")
        assert len(records) == 1
        assert "Caf" in records[0].content


# ──────────────────────────────────────────────────────────
# 8. Registry — get("text/plain") returns a GenericParser
# ──────────────────────────────────────────────────────────

class TestRegistry:
    def test_generic_parser_registered(self) -> None:
        registry = get_registry()
        parser = registry.get("text/plain")
        assert parser is not None
        assert isinstance(parser, GenericParser)

    # ──────────────────────────────────────────────────────────
    # 9. All 4 MIME types registered
    # ──────────────────────────────────────────────────────────
    def test_all_mime_types_registered(self) -> None:
        registry = get_registry()
        expected = [
            "text/plain",
            "text/markdown",
            "text/html",
            "application/xhtml+xml",
        ]
        for mime in expected:
            assert registry.get(mime) is not None, f"MIME type {mime!r} not registered"
