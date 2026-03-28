"""SessionCompressor — converts .tmp session files into structured discovery files.

Uses HaikuSummarizer when available, falls back to HeuristicExtractor.
Idempotent: skips files already compressed (output file exists).
"""
from __future__ import annotations

import logging
from pathlib import Path

from depthfusion.capture.auto_learn import HaikuSummarizer, HeuristicExtractor

logger = logging.getLogger(__name__)

_DEFAULT_DISCOVERIES = Path.home() / ".claude" / "shared" / "discoveries"


class SessionCompressor:
    """Compress a .tmp session file into a discovery markdown file."""

    def __init__(self):
        self._summarizer = HaikuSummarizer()

    def is_available(self) -> bool:
        return self._summarizer.is_available()

    def compress(
        self,
        session_file: Path,
        output_dir: Path | None = None,
    ) -> Path | None:
        """Compress session_file to output_dir.

        Returns the output Path on success, None if skipped (empty or already exists).
        """
        out_dir = output_dir or _DEFAULT_DISCOVERIES
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = session_file.stem
        output_path = out_dir / f"{stem}-autocapture.md"

        if output_path.exists():
            logger.debug("Skipping %s — already compressed", session_file.name)
            return None

        if self.is_available():
            summary = self._summarizer.summarize_file(session_file)
        else:
            summary = HeuristicExtractor().extract_from_file(session_file)

        if not summary:
            return None

        output_path.write_text(summary, encoding="utf-8")
        logger.info("Compressed %s → %s", session_file.name, output_path.name)
        return output_path
