"""Context subscriber — queries the bus for ContextItems matching given tags."""
from __future__ import annotations

from depthfusion.core.types import ContextItem
from depthfusion.router.bus import ContextBus


class ContextSubscriber:
    """Subscribes to a context bus on behalf of a named agent."""

    def __init__(self, bus: ContextBus, agent_name: str) -> None:
        self._bus = bus
        self._agent_name = agent_name

    def query(self, tags: list[str]) -> list[ContextItem]:
        """Get items from the bus matching any of the provided tags."""
        return self._bus.subscribe(tags)
