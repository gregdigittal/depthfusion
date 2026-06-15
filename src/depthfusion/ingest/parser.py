"""Document parser for the ingestion framework (E-53).

Supports:
  - ``.docx`` — via python-docx
  - ``.pdf``  — via pypdf
  - ``.txt``  — plain text UTF-8
  - ``.md``   — Markdown (treated as plain text)

Usage::

    from depthfusion.ingest.parser import DocumentParser

    parser = DocumentParser()
    doc = parser.parse("/path/to/report.pdf", "application/pdf")
    print(doc.text[:200])
"""
from __future__ import annotations

import io
import pathlib
import re

from depthfusion.ingest.models import ParsedDocument

# Sentinel for optional imports — only loaded if the caller actually uses
# that format so the package stays importable even without python-docx /
# pypdf installed.
_docx_available: bool = False
_pypdf_available: bool = False

try:
    import docx as _docx  # python-docx
    _docx_available = True
except ImportError:
    pass

try:
    import pypdf as _pypdf
    _pypdf_available = True
except ImportError:
    pass

# MIME type → extension map used for auto-detection when no mime_type is given.
_EXT_TO_MIME: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

_MIME_TO_EXT: dict[str, str] = {v: k for k, v in _EXT_TO_MIME.items()}

# Heading regex for markdown
_HEADING_RE: re.Pattern[str] = re.compile(r"^#{1,6}\s+(.+)", re.MULTILINE)


class DocumentParser:
    """Parse a file path into a :class:`~depthfusion.ingest.models.ParsedDocument`.

    Args:
        default_acl_allow:   Default ACL principal list applied when the
                             source connector does not supply one.
        default_classification: Default classification label.
    """

    def __init__(
        self,
        default_acl_allow: list[str] | None = None,
        default_classification: str = "internal",
    ) -> None:
        self._default_acl = default_acl_allow or []
        self._default_classification = default_classification

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(
        self,
        path: str,
        mime_type: str | None = None,
        *,
        acl_allow: list[str] | None = None,
        classification: str | None = None,
    ) -> ParsedDocument:
        """Parse a document at *path* and return a :class:`ParsedDocument`.

        Args:
            path:           Absolute or relative file-system path.
            mime_type:      MIME type override.  If omitted, detected from
                            the file extension.
            acl_allow:      Per-document ACL override.
            classification: Per-document classification override.

        Returns:
            A populated :class:`ParsedDocument`.

        Raises:
            ValueError: If the file extension / MIME type is not supported.
            FileNotFoundError: If *path* does not exist.
        """
        p = pathlib.Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        resolved_mime = mime_type or _EXT_TO_MIME.get(p.suffix.lower(), "")
        if not resolved_mime:
            raise ValueError(
                f"Unsupported file extension '{p.suffix}' — "
                f"provide mime_type explicitly or use .docx/.pdf/.txt/.md"
            )

        raw_bytes = p.read_bytes()

        if resolved_mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            text, metadata = self._parse_docx(raw_bytes, str(p))
        elif resolved_mime == "application/pdf":
            text, metadata = self._parse_pdf(raw_bytes, str(p))
        elif resolved_mime in ("text/plain", "text/markdown"):
            text, metadata = self._parse_text(raw_bytes, p.suffix.lower())
        else:
            raise ValueError(f"Unsupported MIME type: {resolved_mime}")

        metadata.setdefault("source_path", str(p))
        metadata.setdefault("filename", p.name)

        return ParsedDocument(
            source_id=str(p.resolve()),
            text=text,
            metadata=metadata,
            acl_allow=acl_allow if acl_allow is not None else list(self._default_acl),
            classification=classification or self._default_classification,
            mime_type=resolved_mime,
        )

    # ------------------------------------------------------------------
    # Format-specific helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_docx(data: bytes, path: str) -> tuple[str, dict[str, str]]:
        """Extract text and metadata from a .docx file."""
        if not _docx_available:
            raise ImportError(
                "python-docx is required for .docx parsing. "
                "Install it with: pip install python-docx"
            )
        doc = _docx.Document(io.BytesIO(data))
        parts: list[str] = []
        for para in doc.paragraphs:
            stripped = para.text.strip()
            if stripped:
                parts.append(stripped)

        # Extract core properties for metadata
        metadata: dict[str, str] = {}
        cp = doc.core_properties
        if cp.title:
            metadata["title"] = str(cp.title)
        if cp.author:
            metadata["author"] = str(cp.author)
        if cp.created:
            metadata["created"] = str(cp.created)

        return "\n\n".join(parts), metadata

    @staticmethod
    def _parse_pdf(data: bytes, path: str) -> tuple[str, dict[str, str]]:
        """Extract text and metadata from a .pdf file."""
        if not _pypdf_available:
            raise ImportError(
                "pypdf is required for .pdf parsing. "
                "Install it with: pip install pypdf"
            )
        reader = _pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            stripped = page_text.strip()
            if stripped:
                parts.append(stripped)

        metadata: dict[str, str] = {}
        info = reader.metadata
        if info:
            if info.title:
                metadata["title"] = str(info.title)
            if info.author:
                metadata["author"] = str(info.author)
            if info.creation_date:
                metadata["created"] = str(info.creation_date)

        return "\n\n".join(parts), metadata

    @staticmethod
    def _parse_text(data: bytes, extension: str) -> tuple[str, dict[str, str]]:
        """Decode plain text or Markdown, extract a best-effort title."""
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")

        metadata: dict[str, str] = {}
        if extension == ".md":
            m = _HEADING_RE.search(text)
            if m:
                metadata["title"] = m.group(1).strip()
        if "title" not in metadata:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    metadata["title"] = stripped[:100]
                    break

        return text, metadata


__all__ = ["DocumentParser"]
