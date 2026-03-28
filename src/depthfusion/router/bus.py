"""Context bus — InMemoryBus (dev/test) and FileBus (production).

The bus enables agents to publish and subscribe to ContextItems with tag-based filtering.
CRITICAL: Tag mismatch = no delivery. CCRS items must never reach VA subscribers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from depthfusion.core.types import ContextItem


@runtime_checkable
class ContextBus(Protocol):
    def publish(self, item: ContextItem) -> None: ...
    def subscribe(self, tags: list[str], source_agent: str | None = None) -> list[ContextItem]: ...
    def clear(self) -> None: ...


class InMemoryBus:
    """Dev/test context bus. All items held in memory."""

    def __init__(self) -> None:
        self._items: list[ContextItem] = []

    def publish(self, item: ContextItem) -> None:
        self._items.append(item)

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
        self._items.clear()


class FileBus:
    """Production context bus. Items stored as JSONL files in bus_dir."""

    _FILENAME = "bus.jsonl"

    def __init__(self, bus_dir: Path) -> None:
        self._bus_dir = bus_dir
        self._bus_dir.mkdir(parents=True, exist_ok=True)
        self._bus_file = self._bus_dir / self._FILENAME

    def publish(self, item: ContextItem) -> None:
        record = {
            "item_id": item.item_id,
            "content": item.content,
            "source_agent": item.source_agent,
            "tags": item.tags,
            "priority": item.priority,
            "ttl_seconds": item.ttl_seconds,
            "metadata": item.metadata,
        }
        with self._bus_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def subscribe(
        self, tags: list[str], source_agent: str | None = None
    ) -> list[ContextItem]:
        """Return items where at least one item tag matches at least one requested tag."""
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

                results.append(ContextItem(
                    item_id=record["item_id"],
                    content=record["content"],
                    source_agent=record["source_agent"],
                    tags=record["tags"],
                    priority=record.get("priority", "normal"),
                    ttl_seconds=record.get("ttl_seconds"),
                    metadata=record.get("metadata", {}),
                ))

        return results

    def clear(self) -> None:
        if self._bus_file.exists():
            self._bus_file.unlink()
