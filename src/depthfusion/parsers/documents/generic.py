"""Generic / best-effort document parser.

Handles text/plain, text/markdown, text/html, and application/xhtml+xml by:

1. Decoding bytes as UTF-8 (BOM-aware), falling back to latin-1 on error.
2. Stripping HTML tags when the text starts with ``<`` (HTML detection).
3. Extracting a title: first markdown heading (``# ...``) or first non-empty line.
4. Extracting heading_path: ordered list of all markdown headings.
5. Chunking into paragraph-sized pieces (max ~2000 chars, split at blank lines;
   long paragraphs further split at sentence boundaries).
6. Returning a single DocumentRecord per call.
"""
from __future__ import annotations

import re

from depthfusion.parsers.documents.base import DocumentParser, DocumentRecord  # noqa: F401

# ──────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────
_MAX_CHUNK_CHARS: int = 2000

# Matches any HTML/XML tag (opening, closing, or self-closing).
# The ``>`` is made optional (``>?``) to handle malformed/dangling opening
# chevrons (e.g. text that ends mid-tag without a closing ``>``).
_TAG_RE: re.Pattern[str] = re.compile(r"<[^>]*>?", re.DOTALL)

# Matches a markdown heading (1–6 ``#`` characters followed by text).
_HEADING_RE: re.Pattern[str] = re.compile(r"^#{1,6}\s+(.+)", re.MULTILINE)

# Sentence-boundary split: period / exclamation / question followed by
# whitespace or end-of-string.
_SENTENCE_END_RE: re.Pattern[str] = re.compile(r"(?<=[.!?])\s+")


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def _decode(data: bytes) -> str:
    """Decode *data* as UTF-8 (BOM-aware), falling back to latin-1."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _strip_html(text: str) -> str:
    """Remove all HTML/XML tags from *text*."""
    # Replace common block-level tags with newlines to preserve structure.
    text = re.sub(r"<(?:br|p|div|li|h[1-6]|tr|td|th)(?:\s[^>]*)?>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags.
    text = _TAG_RE.sub("", text)
    # Collapse runs of whitespace produced by tag removal.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_title(text: str) -> str:
    """Return the first markdown heading or the first non-empty line."""
    m = _HEADING_RE.search(text)
    if m:
        return m.group(1).strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _extract_heading_path(text: str) -> list[str]:
    """Return all markdown heading texts in document order."""
    return [m.group(1).strip() for m in _HEADING_RE.finditer(text)]


def _split_at_sentences(paragraph: str) -> list[str]:
    """Split a long paragraph at sentence boundaries into pieces ≤ _MAX_CHUNK_CHARS.

    If an individual sentence is itself longer than *_MAX_CHUNK_CHARS* it is
    hard-split at exactly *_MAX_CHUNK_CHARS* characters as a final backstop so
    that no returned chunk ever exceeds the limit.
    """
    sentences = _SENTENCE_END_RE.split(paragraph)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        if current_len + len(s) + 1 > _MAX_CHUNK_CHARS and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        # Hard-split a sentence that is itself longer than the max chunk size.
        while len(s) > _MAX_CHUNK_CHARS:
            chunks.append(s[:_MAX_CHUNK_CHARS])
            s = s[_MAX_CHUNK_CHARS:]
        if s:
            current.append(s)
            current_len += len(s) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def _chunk_text(text: str) -> list[str]:
    """Split *text* into paragraph-sized chunks of at most _MAX_CHUNK_CHARS chars.

    Splits primarily at blank lines (paragraph boundaries); if a resulting
    paragraph still exceeds the limit it is further split at sentence boundaries.
    """
    if not text:
        return []

    # Split into paragraphs on one or more blank lines.
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= _MAX_CHUNK_CHARS:
            chunks.append(para)
        else:
            chunks.extend(_split_at_sentences(para))
    return chunks


# ──────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────

class GenericParser:
    """Best-effort fallback parser for plain text, Markdown, and HTML documents."""

    name: str = "generic"
    supported_mime_types: list[str] = [
        "text/plain",
        "text/markdown",
        "text/html",
        "application/xhtml+xml",
    ]

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:  # noqa: D102
        # Guard against callers passing None instead of bytes.
        if data is None:
            data = b""
        text = _decode(data)

        # HTML detection: strip tags when the decoded text starts with ``<``.
        stripped = text.lstrip()
        if stripped.startswith("<"):
            text = _strip_html(text)

        title = _extract_title(text)
        heading_path = _extract_heading_path(text)
        chunks = _chunk_text(text)

        return [
            DocumentRecord(
                source_id=source_id,
                title=title,
                content=text,
                chunks=chunks,
                heading_path=heading_path,
                mime_type="text/plain",  # default; callers may override
            )
        ]


__all__ = ["GenericParser"]
