"""DOCX document parser.

Parses Microsoft Word .docx files using python-docx. Each Heading 1 section
produces one DocumentRecord. Nested headings are tracked in heading_path.

Lazy import: python-docx is optional. If it is not installed this parser
returns an empty list for every call rather than raising.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone

from depthfusion.parsers.documents.base import DocumentParser, DocumentRecord  # noqa: F401

_log = logging.getLogger(__name__)

# python-docx deserialises the entire ZIP into memory.  Files above this limit
# are rejected early rather than blocking the event loop for tens of seconds.
# Pilot feedback: a 48 MB deck caused a 12 s parse stall (FBM-2, 2026-06-19).
_MAX_DOCX_BYTES = 10 * 1024 * 1024  # 10 MB

try:
    from docx import Document

    _docx_available = True
except ImportError:  # pragma: no cover
    _docx_available = False

_MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_HEADING_RE = re.compile(r"^Heading\s+(\d+)$")


class DocxParser:
    """DocumentParser for Word .docx files."""

    name: str = "docx"
    supported_mime_types: list[str] = [_MIME_DOCX]

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        """Parse *data* as a Word document.

        Returns one :class:`DocumentRecord` per Heading 1 section, or an empty
        list if python-docx is unavailable, *data* is empty, or any error occurs.
        """
        if not _docx_available or not data:
            return []

        if len(data) > _MAX_DOCX_BYTES:
            _log.warning(
                "docx parser: %s exceeds %d bytes (%d); skipping to avoid blocking parse "
                "(raise depthfusion.parsers.documents.docx._MAX_DOCX_BYTES to increase limit)",
                source_id,
                _MAX_DOCX_BYTES,
                len(data),
            )
            return []

        try:
            return self._parse_document(source_id, data)
        except Exception:  # noqa: BLE001
            return []

    def _parse_document(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        doc = Document(io.BytesIO(data))
        parse_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: list[DocumentRecord] = []

        heading_stack: list[str] = []
        body_chunks: list[str] = []

        def emit_section() -> None:
            content = "\n".join(body_chunks).strip()
            if not content:
                return

            heading_path = " ▸ ".join(heading_stack)
            records.append(
                DocumentRecord(
                    source_id=source_id,
                    title=heading_stack[0] if heading_stack else source_id,
                    content=content,
                    chunks=[content],
                    heading_path=[heading_path] if heading_path else [],
                    mime_type=_MIME_DOCX,
                    parse_timestamp=parse_ts,
                )
            )

        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue

            style_name = ""
            if paragraph.style is not None and paragraph.style.name is not None:
                style_name = paragraph.style.name

            heading_match = _HEADING_RE.match(style_name)
            if heading_match is not None:
                heading_level = int(heading_match.group(1))

                if heading_level == 1:
                    emit_section()
                    heading_stack = [text]
                    body_chunks = []
                    continue

                if heading_level > 1:
                    heading_stack = heading_stack[: heading_level - 1]
                    heading_stack.append(text)
                    continue

            body_chunks.append(text)

        emit_section()
        return records


__all__ = ["DocxParser"]
