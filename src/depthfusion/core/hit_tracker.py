"""Persistent append-only log of retrieval hits for query-feedback boosting (S-117)."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HIT_LOG_PATH = Path.home() / ".claude" / ".depthfusion_hits.jsonl"
_HIT_WINDOW_SECONDS: int = 30 * 86400   # 30-day rolling window
_PRUNE_SIZE_BYTES: int = 5 * 1024 * 1024  # prune when log exceeds 5 MB


class HitTracker:
    """Persist per-chunk retrieval hit counts for query-feedback scoring.

    Thread-safe. Each instance owns one JSONL log file. Use singleton()
    for the process-wide instance; pass log_path= in tests for isolation.
    """

    _instance: Optional["HitTracker"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self, log_path: Optional[Path] = None) -> None:
        self._path: Path = log_path or _HIT_LOG_PATH
        self._lock = threading.Lock()

    @classmethod
    def singleton(cls, log_path: Optional[Path] = None) -> "HitTracker":
        """Return or create the process-wide HitTracker instance."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(log_path)
            return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        """Clear cached singleton (test helper)."""
        with cls._instance_lock:
            cls._instance = None

    def register_hits(self, chunk_ids: list[str], query: str = "") -> None:
        """Append one JSONL line per chunk_id to the hit log.

        Each line: {"chunk_id": "...", "ts": 1234567890.0, "q": "..."}
        Prunes stale entries (>30 days) if file exceeds _PRUNE_SIZE_BYTES.
        No-op for empty chunk_ids list.
        """
        if not chunk_ids:
            return
        now = time.time()
        lines = [
            json.dumps({"chunk_id": cid, "ts": now, "q": query}) + "\n"
            for cid in chunk_ids
        ]
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.writelines(lines)
            # Prune if file has grown large
            try:
                if self._path.stat().st_size > _PRUNE_SIZE_BYTES:
                    self._prune_stale(now)
            except OSError:
                pass

    def get_hits_30d(self, chunk_id: str) -> int:
        """Return count of hits for chunk_id in the last 30 days."""
        if not self._path.exists():
            return 0
        cutoff = time.time() - _HIT_WINDOW_SECONDS
        count = 0
        with self._lock:
            try:
                with self._path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("chunk_id") == chunk_id and entry.get("ts", 0) > cutoff:
                            count += 1
            except OSError:
                pass
        return count

    def _prune_stale(self, now: Optional[float] = None) -> None:
        """Rewrite log keeping only entries within the 30-day window.

        Must be called under self._lock.
        """
        if now is None:
            now = time.time()
        cutoff = now - _HIT_WINDOW_SECONDS
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            kept = []
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                    if entry.get("ts", 0) > cutoff:
                        kept.append(stripped + "\n")
                except json.JSONDecodeError:
                    pass  # discard corrupt lines
            tmp = self._path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                fh.writelines(kept)
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("HitTracker: prune failed: %s", exc)
