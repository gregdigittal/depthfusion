"""Context publisher — creates and publishes ContextItems to the bus."""
from __future__ import annotations

import uuid

from depthfusion.core.types import ContextItem
from depthfusion.router.bus import ContextBus


class ContextPublisher:
    """Publishes ContextItems to a bus on behalf of a named source agent."""

    def __init__(self, bus: ContextBus, source_agent: str) -> None:
        self._bus = bus
        self._source_agent = source_agent

    def publish(self, content: str, tags: list[str], **kwargs) -> ContextItem:
        """Create a ContextItem and publish it to the bus. Returns the item."""
        priority = kwargs.pop("priority", "normal")
        ttl_seconds = kwargs.pop("ttl_seconds", None)
        metadata = kwargs.pop("metadata", {})
        # Any remaining kwargs go into metadata
        metadata.update(kwargs)

        item = ContextItem(
            item_id=str(uuid.uuid4()),
            content=content,
            source_agent=self._source_agent,
            tags=tags,
            priority=priority,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )
        self._bus.publish(item)
        return item
