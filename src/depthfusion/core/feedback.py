"""FeedbackStore — JSONL-based relevance feedback and weight learning.

Feedback entries are appended to a JSONL file (one JSON object per line).
Source weights are computed as precision-per-source, floored at 0.1,
so no source ever gets completely ignored even with poor initial feedback.

Format is compatible with CLaRa feedback logs if CLaRa is later deployed
(same field names: query, source, chunk_id, relevant, timestamp).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from depthfusion.core.types import FeedbackEntry

_WEIGHT_FLOOR = 0.1


class FeedbackStore:
    """Append-only JSONL store for relevance feedback entries.

    Thread-safety: single-writer assumed (Claude Code is single-agent per session).
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else Path.home() / ".claude" / "depthfusion-feedback.jsonl"

    def append(self, entry: FeedbackEntry) -> None:
        """Append a feedback entry to the JSONL file.

        Adds an ISO-8601 timestamp if the entry has none.
        Creates the file (and parent directories) if they don't exist.
        """
        if entry.timestamp is None:
            entry.timestamp = datetime.now(timezone.utc).isoformat()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def read_all(self) -> list[FeedbackEntry]:
        """Read all feedback entries from the JSONL file.

        Returns an empty list if the file does not exist or is empty.
        Skips malformed lines with a warning rather than crashing.
        """
        if not self.path.exists():
            return []

        entries: list[FeedbackEntry] = []
        with open(self.path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(FeedbackEntry(**data))
                except (json.JSONDecodeError, TypeError) as exc:
                    import warnings
                    warnings.warn(f"Skipping malformed feedback line {lineno}: {exc}")
        return entries

    def learn_source_weights(self) -> dict[str, float]:
        """Compute per-source weights from accumulated feedback.

        Algorithm: precision = (# relevant) / (# total) per source.
        Floor: max(precision, 0.1) — no source is ever fully ignored.

        Returns:
            Mapping of source name → weight in [0.1, 1.0].
            Sources with no feedback are not included (callers should
            default to 1.0 for unknown sources).
        """
        totals: dict[str, int] = {}
        relevant_counts: dict[str, int] = {}

        for entry in self.read_all():
            totals[entry.source] = totals.get(entry.source, 0) + 1
            if entry.relevant:
                relevant_counts[entry.source] = relevant_counts.get(entry.source, 0) + 1

        weights: dict[str, float] = {}
        for source, total in totals.items():
            precision = relevant_counts.get(source, 0) / total
            weights[source] = max(_WEIGHT_FLOOR, precision)

        return weights
