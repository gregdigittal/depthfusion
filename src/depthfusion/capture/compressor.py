"""SessionCompressor — converts .tmp session files into structured discovery files.

Uses HaikuSummarizer when available, falls back to HeuristicExtractor.
Idempotent: skips files already compressed (output file exists).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from depthfusion.capture.auto_learn import HaikuSummarizer, HeuristicExtractor

logger = logging.getLogger(__name__)

_DEFAULT_DISCOVERIES = Path.home() / ".claude" / "shared" / "discoveries"


def idle_sessions(
    sessions_dir: Path,
    min_age_hours: float,
    *,
    now: Optional[datetime] = None,
) -> list[Path]:
    """Return .tmp session files whose mtime is older than min_age_hours.

    A session is "idle" when it hasn't been written to in min_age_hours. This
    is a pure read — it does not modify any files.

    Args:
        sessions_dir: Directory containing .tmp session files.
        min_age_hours: Minimum age in hours before a session is considered idle.
        now: Override current time (for testing). Defaults to UTC now.

    Returns:
        Sorted list (oldest first) of idle .tmp file paths.
    """
    if not sessions_dir.is_dir():
        return []
    if min_age_hours <= 0:
        return []

    cutoff_ts = (now or datetime.now(timezone.utc)).timestamp() - min_age_hours * 3600
    candidates = []
    for p in sessions_dir.iterdir():
        if p.suffix != ".tmp":
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime <= cutoff_ts:
            candidates.append((mtime, p))

    candidates.sort()
    return [p for _, p in candidates]


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
