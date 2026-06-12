"""XLSX document parser (T-594).

Parses Microsoft Excel .xlsx files using openpyxl.  Each worksheet produces one
DocumentRecord. Rows are chunked in groups of MAX_ROWS_PER_CHUNK to keep chunk
sizes manageable.

Lazy import: openpyxl is optional.  If it is not installed this parser
returns an empty list for every call rather than raising.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from depthfusion.parsers.documents.base import DocumentParser, DocumentRecord  # noqa: F401

try:
    import openpyxl

    _openpyxl_available = True
except ImportError:  # pragma: no cover
    _openpyxl_available = False

# ──────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────
MAX_ROWS_PER_CHUNK: int = 50

_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_XLS = "application/vnd.ms-excel"


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────


def _cell_str(value: object) -> str:
    """Convert a cell value to a stripped string, empty string if None."""
    if value is None:
        return ""
    return str(value).strip()


def _row_is_empty(row: tuple[object, ...]) -> bool:
    """Return True if every cell in *row* is None or whitespace."""
    return all(_cell_str(c) == "" for c in row)


def _row_to_text(row: tuple[object, ...]) -> str:
    """Join non-empty cells with ' | ', return empty string for blank rows."""
    parts = [_cell_str(c) for c in row if _cell_str(c)]
    return " | ".join(parts)


def _chunk_rows(rows: list[str], chunk_size: int = MAX_ROWS_PER_CHUNK) -> list[str]:
    """Split a list of row-text strings into chunks of *chunk_size* rows each."""
    chunks: list[str] = []
    for i in range(0, len(rows), chunk_size):
        batch = rows[i : i + chunk_size]
        chunk_text = "\n".join(batch)
        if chunk_text.strip():
            chunks.append(chunk_text)
    return chunks


# ──────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────


class XlsxParser:
    """DocumentParser for Excel .xlsx (and .xls MIME alias) files."""

    name: str = "xlsx"
    supported_mime_types: list[str] = [_MIME_XLSX, _MIME_XLS]

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        """Parse *data* as an Excel workbook.

        Returns one :class:`DocumentRecord` per worksheet, or an empty list
        if openpyxl is unavailable, *data* is empty, or any error occurs.
        """
        if not _openpyxl_available or not data:
            return []

        try:
            return self._parse_workbook(source_id, data)
        except Exception:  # noqa: BLE001
            return []

    def _parse_workbook(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        parse_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records: list[DocumentRecord] = []

        for sheet in wb.worksheets:
            all_rows = list(sheet.iter_rows(values_only=True))

            # Filter to raw values; openpyxl returns tuples of cell values.
            non_empty_rows = [r for r in all_rows if not _row_is_empty(r)]

            if not non_empty_rows:
                # Still emit a record for the sheet even if completely blank.
                records.append(
                    DocumentRecord(
                        source_id=source_id,
                        title=f"Sheet: {sheet.title}",
                        content="",
                        chunks=[],
                        heading_path=[sheet.title],
                        mime_type=_MIME_XLSX,
                        parse_timestamp=parse_ts,
                    )
                )
                continue

            # Table-header inference: first row all non-empty → treat as headers.
            first_row = non_empty_rows[0]
            has_headers = all(_cell_str(c) for c in first_row)

            row_texts: list[str] = []
            for row in non_empty_rows:
                text = _row_to_text(row)
                if text:
                    row_texts.append(text)

            content = "\n".join(row_texts)
            chunks = _chunk_rows(row_texts)

            heading_path: list[str] = [sheet.title]
            if has_headers:
                heading_path.append(_row_to_text(first_row))

            records.append(
                DocumentRecord(
                    source_id=source_id,
                    title=f"Sheet: {sheet.title}",
                    content=content,
                    chunks=chunks,
                    heading_path=heading_path,
                    mime_type=_MIME_XLSX,
                    parse_timestamp=parse_ts,
                )
            )

        return records


__all__ = ["XlsxParser"]
