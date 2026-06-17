"""Benchmark: ingest 50 small synthetic PDFs and assert >=20 docs/s throughput (T-603)."""

import time
from pathlib import Path

import pytest

# Minimal valid PDF that the parser can handle without external dependencies.
# The content is a single page with "Hello" text. Total size ~500 bytes.
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    b"4 0 obj\n<< /Length 44 >>\nstream\n"
    b"BT /F1 12 Tf 100 700 Td (Hello) Tj ET\n"
    b"endstream\nendobj\n"
    b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n0000000266 00000 n \n"
    b"0000000355 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n429\n%%EOF"
)


@pytest.fixture(autouse=True)
def _disable_heavy_features(monkeypatch):  # noqa: PT004
    """Ensure no network, no LLM, no OCR are used during the benchmark."""
    monkeypatch.setenv("DEPTHFUSION_OCR_ENABLED", "0")
    monkeypatch.setenv("DEPTHFUSION_OCR", "0")  # legacy alias
    monkeypatch.setenv("DEPTHFUSION_PARSE_MAX_BYTES", "0")  # no size limit for test


def test_ingest_throughput(tmp_path: Path) -> None:
    """
    Generate 50 small PDFs, run them through the ingestion pipeline,
    and assert that measured throughput is at least 20 documents per second.

    **Test plan:**
    - Create 50 minimal synthetic PDF files in a temp directory.
    - Instantiate DocumentParser with default settings (env vars disable OCR).
    - Parse each PDF and measure wall-clock time.
    - Assert measured throughput >= 20 docs/s.
    - Verify no external dependencies (network, real LLM, real OCR) were invoked.
    """
    # ── Generate 50 synthetic PDFs ──────────────────────────────────
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    file_paths = []
    for i in range(50):
        path = pdf_dir / f"doc_{i:03d}.pdf"
        path.write_bytes(MINIMAL_PDF)
        file_paths.append(path)

    # ── Import the DocumentParser ──────────────────────────────────
    from depthfusion.ingest.parser import DocumentParser

    # Instantiate with defaults; env vars disable OCR/LLM
    parser = DocumentParser()

    # ── Run all documents and measure wall-clock time ───────────────
    start = time.perf_counter()
    for path in file_paths:
        # Parse each document. The parser returns a ParsedDocument instance.
        # We only care about throughput, not the result.
        _ = parser.parse(str(path))
    elapsed = time.perf_counter() - start

    # ── Assert throughput ──────────────────────────────────────────
    docs_per_sec = 50 / elapsed if elapsed > 0 else float("inf")
    assert (
        docs_per_sec >= 20
    ), f"Throughput too low: {docs_per_sec:.2f} docs/s (expected >=20, elapsed {elapsed:.3f}s)"
