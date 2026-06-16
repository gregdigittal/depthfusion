"""OCR document parser (T-598).

Extracts text from raster images (PNG, JPEG) using pytesseract or
rapidocr_onnxruntime as OCR backends.

Feature flag: set the environment variable ``DEPTHFUSION_OCR_ENABLED=1``
to activate this parser.  When the variable is absent, empty, or ``0`` the
:class:`OcrParser` returns an empty list for every call — no import of any
OCR library is attempted.

Lazy imports: pytesseract and rapidocr_onnxruntime are both optional.  The
parser tries pytesseract first; if unavailable it falls back to
rapidocr_onnxruntime.  If neither backend is importable when OCR is enabled,
parsing gracefully returns an empty list rather than raising.

Usage::

    # With DEPTHFUSION_OCR_ENABLED=1 in the environment:
    from depthfusion.parsers.documents.ocr import OcrParser
    parser = OcrParser()
    records = parser.parse("scan-001", png_bytes)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from depthfusion.parsers.documents.base import DocumentParser, DocumentRecord  # noqa: F401

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def _ocr_enabled() -> bool:
    """Return True when DEPTHFUSION_OCR_ENABLED is set to a truthy value."""
    raw = os.environ.get("DEPTHFUSION_OCR_ENABLED", "")
    return raw.strip() not in ("", "0", "false", "False", "no", "No")


# ---------------------------------------------------------------------------
# Lazy backend detection
# ---------------------------------------------------------------------------

def _try_import_pytesseract():  # type: ignore[return]
    """Return the pytesseract module if importable, else None."""
    try:
        import pytesseract  # type: ignore[import]
        return pytesseract
    except ImportError:
        return None


def _try_import_rapidocr():  # type: ignore[return]
    """Return a callable RapidOCR instance if importable, else None."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import]
        return RapidOCR()
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def _extract_with_pytesseract(pytesseract_mod, data: bytes) -> str:
    """Run pytesseract on *data* and return the extracted string."""
    import io

    from PIL import Image  # type: ignore[import]

    image = Image.open(io.BytesIO(data))
    return pytesseract_mod.image_to_string(image)


def _extract_with_rapidocr(ocr_instance, data: bytes) -> str:
    """Run RapidOCR on *data* and return extracted text joined by newlines."""
    import io

    import numpy as np  # type: ignore[import]
    from PIL import Image  # type: ignore[import]

    image = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.array(image)
    result, _ = ocr_instance(arr)
    if not result:
        return ""
    # result is a list of (bbox, text, score) tuples
    lines = [item[1] for item in result if item[1]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_MIME_PNG = "image/png"
_MIME_JPEG = "image/jpeg"
_IMAGE_MIME_TYPES: list[str] = [_MIME_PNG, _MIME_JPEG]


class OcrParser:
    """DocumentParser for raster images using OCR.

    Behaviour:
    - When ``DEPTHFUSION_OCR_ENABLED`` is unset or ``0``: returns ``[]``.
    - When enabled but no OCR backend is importable: returns ``[]``.
    - When enabled and a backend is available: runs OCR and returns one
      :class:`DocumentRecord` containing the extracted text.
    - Any runtime error during OCR returns ``[]`` rather than raising.
    """

    name: str = "ocr"
    supported_mime_types: list[str] = _IMAGE_MIME_TYPES

    def parse(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        """Parse image bytes via OCR.

        Args:
            source_id: Stable identifier for the source document.
            data:      Raw image bytes (PNG or JPEG).

        Returns:
            A list containing one :class:`DocumentRecord`, or an empty list
            when OCR is disabled, a backend is unavailable, or an error occurs.
        """
        # Gate 1: feature flag
        if not _ocr_enabled():
            return []

        # Gate 2: no data
        if not data:
            return []

        # Gate 3: attempt OCR with available backend
        try:
            return self._run_ocr(source_id, data)
        except Exception:  # noqa: BLE001
            return []

    def _run_ocr(self, source_id: str, data: bytes) -> list[DocumentRecord]:
        """Attempt OCR with pytesseract, falling back to rapidocr.

        Returns an empty list when no backend is available or text is empty.
        """
        text: str | None = None

        # Try pytesseract first
        pytesseract_mod = _try_import_pytesseract()
        if pytesseract_mod is not None:
            try:
                text = _extract_with_pytesseract(pytesseract_mod, data)
            except Exception:  # noqa: BLE001
                text = None

        # Fall back to rapidocr
        if text is None:
            ocr_instance = _try_import_rapidocr()
            if ocr_instance is not None:
                try:
                    text = _extract_with_rapidocr(ocr_instance, data)
                except Exception:  # noqa: BLE001
                    text = None

        # No backend succeeded
        if text is None:
            return []

        text = text.strip()
        if not text:
            return []

        parse_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Simple chunking: split on blank lines
        chunks = [p.strip() for p in text.split("\n\n") if p.strip()]

        return [
            DocumentRecord(
                source_id=source_id,
                title=source_id,
                content=text,
                chunks=chunks,
                heading_path=[],
                mime_type=_MIME_PNG,  # generic; callers may override
                parse_timestamp=parse_ts,
            )
        ]


__all__ = ["OcrParser", "_IMAGE_MIME_TYPES", "_MIME_JPEG", "_MIME_PNG"]
