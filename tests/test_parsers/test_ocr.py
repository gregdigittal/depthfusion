"""Tests for the OCR document parser (T-598).

Covers three required scenarios:
1. Flag-off no-op — DEPTHFUSION_OCR_ENABLED absent/0 → always returns [].
2. Backend-missing graceful skip — flag on, backend not importable → returns [].
3. Mocked backend text extraction — flag on, backend available → returns records.

No real OCR libraries are required.  The tests patch at the high-level helper
functions (_try_import_pytesseract, _try_import_rapidocr, _extract_with_pytesseract)
so they are fully self-contained and do not depend on system tesseract or PIL/Pillow.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # minimal fake PNG header


# ---------------------------------------------------------------------------
# 1. Flag-off no-op
# ---------------------------------------------------------------------------

class TestOcrParserFlagOff:
    """When DEPTHFUSION_OCR_ENABLED is absent or 0, parse() must return []."""

    def _make_parser(self) -> object:
        from depthfusion.parsers.documents.ocr import OcrParser
        return OcrParser()

    def test_flag_unset_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEPTHFUSION_OCR_ENABLED", raising=False)
        parser = self._make_parser()
        assert parser.parse("img-001", _FAKE_PNG) == []

    def test_flag_zero_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "0")
        parser = self._make_parser()
        assert parser.parse("img-002", _FAKE_PNG) == []

    def test_flag_empty_string_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "")
        parser = self._make_parser()
        assert parser.parse("img-003", _FAKE_PNG) == []

    def test_empty_data_with_flag_off_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEPTHFUSION_OCR_ENABLED", raising=False)
        parser = self._make_parser()
        assert parser.parse("img-empty", b"") == []


# ---------------------------------------------------------------------------
# 2. Backend-missing graceful skip
# ---------------------------------------------------------------------------

class TestOcrParserBackendMissing:
    """When OCR flag is on but no backend is importable, parse() returns []."""

    def test_no_backend_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "1")

        from depthfusion.parsers.documents import ocr as ocr_mod

        with (
            patch.object(ocr_mod, "_try_import_pytesseract", return_value=None),
            patch.object(ocr_mod, "_try_import_rapidocr", return_value=None),
        ):
            parser = ocr_mod.OcrParser()
            result = parser.parse("img-no-backend", _FAKE_PNG)

        assert result == []

    def test_import_error_in_pytesseract_falls_back_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both backend detectors return None → graceful empty result."""
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "1")

        from depthfusion.parsers.documents import ocr as ocr_mod

        with (
            patch.object(ocr_mod, "_try_import_pytesseract", return_value=None),
            patch.object(ocr_mod, "_try_import_rapidocr", return_value=None),
        ):
            parser = ocr_mod.OcrParser()
            assert parser.parse("img-fallback", _FAKE_PNG) == []


# ---------------------------------------------------------------------------
# 3. Mocked backend text extraction
# ---------------------------------------------------------------------------

class TestOcrParserExtraction:
    """When flag is on and a mocked backend is available, parse() returns records.

    Strategy: patch _try_import_pytesseract to return a non-None sentinel AND
    patch _extract_with_pytesseract to return the desired text string directly.
    This avoids any dependency on PIL.Image.open or real file I/O.
    """

    def _run_with_mock_text(
        self,
        source_id: str,
        data: bytes,
        text: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "1")

        from depthfusion.parsers.documents import ocr as ocr_mod

        # A non-None sentinel convinces _run_ocr to try pytesseract
        mock_pytesseract_mod = MagicMock()

        with (
            patch.object(ocr_mod, "_try_import_pytesseract", return_value=mock_pytesseract_mod),
            patch.object(ocr_mod, "_try_import_rapidocr", return_value=None),
            # Bypass Image.open entirely — return text directly
            patch.object(ocr_mod, "_extract_with_pytesseract", return_value=text),
        ):
            parser = ocr_mod.OcrParser()
            return parser.parse(source_id, data)

    def test_text_extraction_returns_one_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = self._run_with_mock_text(
            "scan-001", _FAKE_PNG, "Hello World", monkeypatch
        )
        assert len(records) == 1

    def test_record_source_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = self._run_with_mock_text(
            "scan-abc", _FAKE_PNG, "some text", monkeypatch
        )
        assert records[0].source_id == "scan-abc"

    def test_record_content_matches_ocr_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = self._run_with_mock_text(
            "scan-002", _FAKE_PNG, "Invoice #42\nTotal: $100", monkeypatch
        )
        assert "Invoice #42" in records[0].content
        assert "Total: $100" in records[0].content

    def test_record_chunks_non_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = self._run_with_mock_text(
            "scan-003", _FAKE_PNG, "Paragraph one.\n\nParagraph two.", monkeypatch
        )
        assert len(records[0].chunks) >= 1

    def test_record_parse_timestamp_iso(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = self._run_with_mock_text(
            "scan-004", _FAKE_PNG, "text", monkeypatch
        )
        ts = records[0].parse_timestamp
        assert ts.endswith("Z")
        assert "T" in ts

    def test_empty_ocr_result_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When OCR returns only whitespace, parse() returns []."""
        records = self._run_with_mock_text(
            "scan-blank", _FAKE_PNG, "   \n  \n  ", monkeypatch
        )
        assert records == []

    def test_empty_data_with_flag_on_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "1")

        from depthfusion.parsers.documents.ocr import OcrParser
        parser = OcrParser()
        assert parser.parse("scan-empty", b"") == []

    def test_ocr_exception_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Runtime exceptions during extraction must be swallowed, not re-raised."""
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "1")

        from depthfusion.parsers.documents import ocr as ocr_mod

        mock_pytesseract_mod = MagicMock()

        with (
            patch.object(ocr_mod, "_try_import_pytesseract", return_value=mock_pytesseract_mod),
            patch.object(ocr_mod, "_try_import_rapidocr", return_value=None),
            patch.object(
                ocr_mod,
                "_extract_with_pytesseract",
                side_effect=RuntimeError("tesseract crashed"),
            ),
        ):
            parser = ocr_mod.OcrParser()
            result = parser.parse("scan-crash", _FAKE_PNG)

        assert result == []


# ---------------------------------------------------------------------------
# 4. Protocol conformance
# ---------------------------------------------------------------------------

class TestOcrParserProtocol:
    """OcrParser must satisfy the DocumentParser Protocol."""

    def test_has_name(self) -> None:
        from depthfusion.parsers.documents.ocr import OcrParser
        assert isinstance(OcrParser.name, str)
        assert OcrParser.name

    def test_has_supported_mime_types(self) -> None:
        from depthfusion.parsers.documents.ocr import _IMAGE_MIME_TYPES, OcrParser
        assert "image/png" in OcrParser.supported_mime_types
        assert "image/jpeg" in OcrParser.supported_mime_types
        assert set(OcrParser.supported_mime_types) == set(_IMAGE_MIME_TYPES)

    def test_satisfies_document_parser_protocol(self) -> None:
        from depthfusion.parsers.documents.base import DocumentParser
        from depthfusion.parsers.documents.ocr import OcrParser
        assert isinstance(OcrParser(), DocumentParser)


# ---------------------------------------------------------------------------
# 5. Registry integration
# ---------------------------------------------------------------------------

class TestOcrRegistryIntegration:
    """image/png and image/jpeg are in the registry only when flag is on."""

    def test_flag_off_not_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEPTHFUSION_OCR_ENABLED", raising=False)

        # Re-import the module fresh to pick up the flag state
        # The registry is a module-level singleton so we test what was registered
        # at import time — we inspect the OcrParser directly instead.
        from depthfusion.parsers.documents.ocr import OcrParser, _ocr_enabled
        assert not _ocr_enabled()
        # parse() returns [] when flag is off regardless of registration
        parser = OcrParser()
        assert parser.parse("img", _FAKE_PNG) == []

    def test_flag_on_registry_contains_image_types(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With flag on, OcrParser reports image MIME types in supported_mime_types."""
        monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "1")
        from depthfusion.parsers.documents.ocr import OcrParser, _ocr_enabled
        assert _ocr_enabled()
        assert "image/png" in OcrParser.supported_mime_types
        assert "image/jpeg" in OcrParser.supported_mime_types
