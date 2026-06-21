"""PDF text-layer document parser.

Extracts plain text from PDF text layers without OCR.  When pdfplumber is
available, pages are parsed individually using its layout-aware text extraction
and each page produces one DocumentRecord.  If pdfplumber is unavailable, the
parser falls back to pdfminer.six and emits a single full-document record.

Lazy imports: pdfplumber and pdfminer.six are optional.  If neither library is
installed, this parser returns an empty list for every call rather than raising.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from depthfusion.parsers.documents.base import DocumentParser, DocumentRecord

try:
    import pdfplumber

    _pdfplumber_available = True
except ImportError:  # pragma: no cover
    _pdfplumber_available = False

try:
    import pdfminer.high_level as _pdfminer

    _pdfminer_available = True
except ImportError:  # pragma: no cover
    _pdfminer_available = False

_MIME_PDF = "application/pdf"


class PdfParser(DocumentParser):
    """DocumentParser for PDF text layers."""

    name: str = "pdf"
    supported_mime_types: list[str] = [_MIME_PDF]

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        """Parse *data* as a PDF document.

        Returns one :class:`DocumentRecord` per page via pdfplumber, a single
        record via pdfminer fallback, or an empty list if dependencies are
        unavailable, *data* is empty, or any error occurs.
        """
        if not data or not (_pdfplumber_available or _pdfminer_available):
            return []

        try:
            if _pdfplumber_available:
                return self._parse_with_pdfplumber(source_id, data)
            return self._parse_with_pdfminer(source_id, data)
        except Exception:  # noqa: BLE001
            return []

    def _parse_with_pdfplumber(
        self,
        document_id: str,
        content_bytes: bytes,
    ) -> list[DocumentRecord]:
        parse_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: list[DocumentRecord] = []

        with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
            for i, page in enumerate(pdf.pages):
                heading = f"Page {i + 1}"
                content = (page.extract_text() or "").strip()

                records.append(
                    DocumentRecord(
                        source_id=document_id,
                        title=heading,
                        content=content,
                        chunks=[content] if content else [],
                        heading_path=[heading],
                        mime_type=_MIME_PDF,
                        parse_timestamp=parse_ts,
                    )
                )

        return records

    def _parse_with_pdfminer(
        self,
        document_id: str,
        content_bytes: bytes,
    ) -> list[DocumentRecord]:
        parse_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        content = (_pdfminer.extract_text(io.BytesIO(content_bytes)) or "").strip()

        return [
            DocumentRecord(
                source_id=document_id,
                title=document_id,
                content=content,
                chunks=[content] if content else [],
                heading_path=[],
                mime_type=_MIME_PDF,
                parse_timestamp=parse_ts,
            )
        ]


__all__ = ["PdfParser"]
