"""Context bus — InMemoryBus (dev/test) and FileBus (production).

The bus enables agents to publish and subscribe to ContextItems with tag-based
filtering. CRITICAL: tag mismatch = no delivery — CCRS items must never reach
VA subscribers.

S-78 (publish_context idempotency): both bus implementations dedup at publish
time by ``ContextItem.content_hash``. Re-publishing identical content returns
``{"published": True, "item_id": <original>, "deduped": True}`` rather than
creating a second row. Legacy ``bus.jsonl`` rows written before S-78 lack a
``content_hash`` field and are intentionally never matched for dedup (AC-6).
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from depthfusion.core.types import ContextItem


@runtime_checkable
class ContextBus(Protocol):
    def publish(self, item: ContextItem) -> dict[str, Any]: ...
    def subscribe(self, tags: list[str], source_agent: str | None = None) -> list[ContextItem]: ...
    def clear(self) -> None: ...


def _publish_result(item_id: str, deduped: bool) -> dict[str, Any]:
    """Canonical publish-result shape (S-78 AC-4)."""
    return {"published": True, "item_id": item_id, "deduped": deduped}


class InMemoryBus:
    """Dev/test context bus. All items held in memory."""

    def __init__(self) -> None:
        self._items: list[ContextItem] = []
        # Maps content_hash → original item_id of first occurrence. Empty/None
        # hashes are never indexed, so legacy items can't collide.
        self._hash_index: dict[str, str] = {}
        self._lock = threading.Lock()

    def publish(self, item: ContextItem) -> dict[str, Any]:
        h = item.content_hash or ""
        with self._lock:
            if h and h in self._hash_index:
                return _publish_result(self._hash_index[h], deduped=True)
            self._items.append(item)
            if h:
                self._hash_index[h] = item.item_id
            return _publish_result(item.item_id, deduped=False)

    def subscribe(
        self, tags: list[str], source_agent: str | None = None
    ) -> list[ContextItem]:
        """Return items where at least one item tag matches at least one requested tag."""
        if not tags:
            return []
        tag_set = set(tags)
        results = []
        for item in self._items:
            item_tag_set = set(item.tags)
            if not item_tag_set & tag_set:
                continue
            if source_agent is not None and item.source_agent != source_agent:
                continue
            results.append(item)
        return results

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._hash_index.clear()


class FileBus:
    """Production context bus. Items stored as JSONL in bus_dir.

    Concurrency model: an intra-process ``threading.Lock`` serializes writes
    from threads in this process; an inter-process ``fcntl.flock`` serializes
    writes across processes that share the same ``bus.jsonl``. The flock is
    held for the read-check-write critical section so a sibling process cannot
    insert a duplicate between our scan and our append.
    """

    _FILENAME = "bus.jsonl"

    def __init__(self, bus_dir: Path) -> None:
        self._bus_dir = bus_dir
        self._bus_dir.mkdir(parents=True, exist_ok=True)
        self._bus_file = self._bus_dir / self._FILENAME
        self._lock = threading.Lock()
        # Best-effort warm cache. The authoritative dedup decision is always
        # made under flock against a freshly-re-scanned bus.jsonl.
        self._hash_index: dict[str, str] = self._scan_hash_index_unlocked()

    def _scan_hash_index_unlocked(self) -> dict[str, str]:
        """Scan bus.jsonl and build content_hash → item_id index.

        Legacy rows (no content_hash key, or empty content_hash) are skipped:
        they are never matched for dedup (AC-6). First occurrence wins on
        duplicate hashes (defensive — pre-S-78 deployments may have duplicates).
        Structurally invalid records (JSON-decodable but not dict, or missing
        item_id) are skipped — bad rows must not poison the index or crash init.
        """
        index: dict[str, str] = {}
        if not self._bus_file.exists():
            return index
        try:
            with self._bus_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    h = record.get("content_hash")
                    item_id = record.get("item_id")
                    if h and isinstance(item_id, str) and item_id and h not in index:
                        index[h] = item_id
        except OSError:
            # If the file disappears between exists() and open(), return what we have.
            pass
        return index

    def publish(self, item: ContextItem) -> dict[str, Any]:
        h = item.content_hash or ""
        with self._lock:
            # Open in 'a+' to combine read (for fresh under-lock scan) and append.
            # We never read past the index-build phase; the append always lands at EOF.
            with self._bus_file.open("a+", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    # Re-scan under lock so writes from sibling processes since
                    # __init__ are observed.
                    f.seek(0)
                    current_index: dict[str, str] = {}
                    last_byte: bytes | None = None  # tracks final byte for trailing-newline guard
                    for row_line in f:
                        last_byte = row_line.encode("utf-8")[-1:] if row_line else last_byte
                        stripped = row_line.strip()
                        if not stripped:
                            continue
                        try:
                            row_record = json.loads(stripped)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(row_record, dict):
                            continue
                        rh = row_record.get("content_hash")
                        rid = row_record.get("item_id")
                        if rh and isinstance(rid, str) and rid and rh not in current_index:
                            current_index[rh] = rid

                    if h and h in current_index:
                        original_id = current_index[h]
                        # Refresh in-process warm cache so we don't keep
                        # redundantly re-scanning for the same hash.
                        self._hash_index[h] = original_id
                        return _publish_result(original_id, deduped=True)

                    # Not a duplicate — prepare and write new record.
                    new_record = {
                        "item_id": item.item_id,
                        "content": item.content,
                        "source_agent": item.source_agent,
                        "tags": item.tags,
                        "priority": item.priority,
                        "ttl_seconds": item.ttl_seconds,
                        "metadata": item.metadata,
                        "content_hash": item.content_hash,
                        # S-70 — scoring scalars travel with the bus row so
                        # consumers can read them without a separate lookup.
                        "importance": item.importance,
                        "salience": item.salience,
                    }
                    f.seek(0, os.SEEK_END)
                    # If a prior crash left a torn write (file ends mid-line, no
                    # trailing newline), prepend a separator newline so our new
                    # record lands on its own line and stays parseable. Both the
                    # torn fragment and the new record are then individually
                    # JSON-decode-attempted by future scanners; the fragment is
                    # silently skipped while the new record reads cleanly.
                    needs_separator = last_byte is not None and last_byte != b"\n"
                    if needs_separator:
                        f.write("\n")
                    f.write(json.dumps(new_record) + "\n")
                    f.flush()
                    os.fsync(f.fileno())

                    # Cache-on-success: only update the warm cache after the
                    # bytes are durably on disk. If flush/fsync raised, this
                    # line never runs and the next publish attempt cleanly
                    # retries instead of being blocked by a phantom hash.
                    if h:
                        self._hash_index[h] = item.item_id

                    return _publish_result(item.item_id, deduped=False)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def subscribe(
        self, tags: list[str], source_agent: str | None = None
    ) -> list[ContextItem]:
        """Return items where at least one item tag matches at least one requested tag.

        Legacy rows lacking ``content_hash`` are reconstructed with
        ``content_hash=""`` (empty-string sentinel), which is preserved verbatim
        by ``ContextItem.__post_init__`` and remains ineligible for dedup (AC-6).
        """
        if not tags or not self._bus_file.exists():
            return []

        tag_set = set(tags)
        results = []

        with self._bus_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                item_tags = set(record.get("tags", []))
                if not item_tags & tag_set:
                    continue
                if source_agent is not None and record.get("source_agent") != source_agent:
                    continue

                # AC-6: legacy row → preserve absence of hash by passing "".
                # Post-S-78 row → pass the stored hash so __post_init__ keeps it.
                stored_hash = record.get("content_hash")
                content_hash_for_item = "" if stored_hash is None else stored_hash

                # S-70 consensus: malformed score field (e.g. string-typed
                # importance from a third-party-written row) must not crash
                # subscribe — skip the bad row and continue, mirroring the
                # malformed-JSON path above.
                try:
                    results.append(ContextItem(
                        item_id=record["item_id"],
                        content=record["content"],
                        source_agent=record["source_agent"],
                        tags=record["tags"],
                        priority=record.get("priority", "normal"),
                        ttl_seconds=record.get("ttl_seconds"),
                        metadata=record.get("metadata", {}),
                        content_hash=content_hash_for_item,
                        # Legacy rows lacking these fields parse back as
                        # canonical defaults via ContextItem.__post_init__.
                        importance=record.get("importance"),
                        salience=record.get("salience"),
                    ))
                except (TypeError, ValueError):
                    continue

        return results

    def clear(self) -> None:
        """Clear the bus, holding the same flock used by publish().

        Truncate-under-flock (rather than unlink) keeps the inode live so that
        a concurrent publish() in another process — which already opened a file
        descriptor against the original inode — does not silently write to an
        orphaned inode. The publisher will block on flock, observe a freshly
        empty file under the lock, and behave correctly.
        """
        with self._lock:
            # Touch the file (in case it doesn't exist yet) so we have something
            # to flock against. open() in 'a' creates if absent.
            with self._bus_file.open("a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.truncate(0)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            self._hash_index.clear()
