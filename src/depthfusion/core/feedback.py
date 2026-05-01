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


# ---------------------------------------------------------------------------
# E-27 / S-72 — in-memory recall store for salience feedback
# ---------------------------------------------------------------------------

import logging
import threading
import time
import uuid
from dataclasses import asdict as _asdict
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from typing import Optional as _Optional

_logger = logging.getLogger(__name__)

USED_BOOST: float = 0.1
IGNORED_DECAY: float = 0.05
RECALL_TTL_SECONDS: int = 86400  # 24h per AC-2


def _discoveries_dir() -> "Path":
    """Resolve the discoveries directory. Patchable for tests."""
    return Path.home() / ".claude" / "shared" / "discoveries"


@_dataclass
class _RecallEntry:
    ts: float
    chunk_ids: list
    applied: set = _field(default_factory=set)


@_dataclass
class FeedbackResult:
    """Bucket counts returned by ``RecallStore.apply_feedback``.

    Each input chunk_id lands in exactly one bucket.
    """
    ok: bool
    applied: int = 0
    skipped_unsupported: int = 0
    skipped_missing: int = 0
    skipped_already_applied: int = 0
    skipped_expired: int = 0
    error: _Optional[str] = None

    def to_dict(self) -> dict:
        d = _asdict(self)
        if d["error"] is None:
            d.pop("error")
        return d


def _chunk_id_to_file_stem(chunk_id: str) -> str:
    """Extract the file stem from a chunk_id of form '{stem}#{i}' or '{stem}'."""
    if "#" in chunk_id:
        return chunk_id.split("#", 1)[0]
    return chunk_id


def _resolve_discovery_file(file_stem: str) -> "_Optional[Path]":
    """Return the live discovery file path for a given chunk's file_stem.

    Returns None if the file is missing, archived (under .archive/), or
    superseded — all of which count as skipped_missing in the response.
    """
    base = _discoveries_dir()
    candidate = base / f"{file_stem}.md"
    if candidate.is_file():
        return candidate
    return None


class RecallStore:
    """Process-wide in-memory store of recall_id → chunk_ids + applied-set.

    Sweep-on-write TTL eviction (entries with ts > 24h are deleted on the
    next register_recall call). All public mutation paths are serialized by
    a single threading.Lock.
    """

    _singleton: "_Optional[RecallStore]" = None
    _singleton_lock = threading.Lock()

    @classmethod
    def singleton(cls) -> "RecallStore":
        """Return the process-wide instance (lazy-init under lock)."""
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    @classmethod
    def reset_singleton(cls) -> None:
        """Test helper — drops the cached instance."""
        with cls._singleton_lock:
            cls._singleton = None

    def __init__(self) -> None:
        self._entries: dict = {}
        self._lock = threading.Lock()

    def register_recall(self, chunk_ids: list) -> str:
        """Mint a new recall_id; sweep stale; insert; return the id."""
        rid = str(uuid.uuid4())
        now = time.time()
        with self._lock:
            cutoff = now - RECALL_TTL_SECONDS
            self._entries = {
                k: v for k, v in self._entries.items() if v.ts > cutoff
            }
            self._entries[rid] = _RecallEntry(ts=now, chunk_ids=list(chunk_ids))
        return rid

    def apply_feedback(
        self,
        recall_id: str,
        used: list,
        ignored: list,
    ) -> "FeedbackResult":
        """Apply bounded salience deltas. See module docstring for semantics."""
        from collections import defaultdict

        from depthfusion.capture.dedup import extract_memory_score
        from depthfusion.core.file_locking import atomic_frontmatter_rewrite

        result = FeedbackResult(ok=True)

        # Hold self._lock for the entire operation so concurrent calls for the
        # same recall_id cannot observe the same already_applied snapshot and
        # double-apply deltas. atomic_frontmatter_rewrite uses a separate
        # fcntl sidecar lock — no deadlock risk.
        with self._lock:
            entry = self._entries.get(recall_id)
            now = time.time()
            if entry is None:
                result.skipped_missing = len(used) + len(ignored)
                return result
            if now - entry.ts > RECALL_TTL_SECONDS:
                del self._entries[recall_id]
                result.skipped_expired = len(used) + len(ignored)
                return result
            already_applied = set(entry.applied)
            registered = set(entry.chunk_ids)

            per_file_used: dict = defaultdict(int)
            per_file_ignored: dict = defaultdict(int)
            chunks_to_mark_applied: dict = defaultdict(list)

            for chunk_id in used:
                outcome = self._bucket_chunk(chunk_id, registered, already_applied)
                if outcome == "already":
                    result.skipped_already_applied += 1
                elif outcome == "unsupported":
                    result.skipped_unsupported += 1
                elif outcome == "missing":
                    result.skipped_missing += 1
                else:
                    target = outcome
                    per_file_used[target] += 1
                    chunks_to_mark_applied[target].append(chunk_id)

            for chunk_id in ignored:
                outcome = self._bucket_chunk(chunk_id, registered, already_applied)
                if outcome == "already":
                    result.skipped_already_applied += 1
                elif outcome == "unsupported":
                    result.skipped_unsupported += 1
                elif outcome == "missing":
                    result.skipped_missing += 1
                else:
                    target = outcome
                    per_file_ignored[target] += 1
                    chunks_to_mark_applied[target].append(chunk_id)

            for target in set(per_file_used) | set(per_file_ignored):
                delta = (
                    USED_BOOST * per_file_used.get(target, 0)
                    - IGNORED_DECAY * per_file_ignored.get(target, 0)
                )
                try:
                    with atomic_frontmatter_rewrite(target) as ctx:
                        current = extract_memory_score(ctx.body)
                        ctx.set_score(salience=current.salience + delta)
                except FileNotFoundError:
                    count = per_file_used.get(target, 0) + per_file_ignored.get(target, 0)
                    result.skipped_missing += count
                    continue
                except OSError as exc:
                    _logger.warning(
                        "recall_feedback: lock/write failed for %s: %s", target, exc,
                    )
                    count = per_file_used.get(target, 0) + per_file_ignored.get(target, 0)
                    result.skipped_missing += count
                    continue
                result.applied += per_file_used.get(target, 0) + per_file_ignored.get(target, 0)
                for chunk_id in chunks_to_mark_applied[target]:
                    entry.applied.add(chunk_id)

        return result

    def _bucket_chunk(
        self,
        chunk_id: str,
        registered: set,
        already_applied: set,
    ) -> object:
        """Classify a single chunk_id.

        Returns:
          'already'     — chunk_id is in already_applied
          'unsupported' — chunk_id was NOT in the registered set for this recall
          'missing'     — chunk_id IS registered but discovery file unresolvable
          Path          — live discovery file path (apply delta)
        """
        if chunk_id in already_applied:
            return "already"
        if chunk_id not in registered:
            return "unsupported"
        file_stem = _chunk_id_to_file_stem(chunk_id)
        target = _resolve_discovery_file(file_stem)
        if target is None:
            return "missing"
        return target
