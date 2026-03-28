"""Core type definitions for DepthFusion.

Dataclasses for retrieved chunks, session blocks, context items, and feedback,
plus Protocol definitions for pluggable embedding and storage backends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class RetrievedChunk:
    """A chunk of content retrieved from any memory source."""
    chunk_id: str
    content: str
    source: str          # e.g. "session_file", "memory", "context_bus"
    score: float
    rank: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionBlock:
    """A scored block within a session file, used for selective loading."""
    session_id: str
    block_index: int
    content: str
    tags: list[str]
    relevance_score: float = 0.0
    embedding: Optional[list[float]] = None


@dataclass
class ContextItem:
    """An item published to the context bus by one agent, consumed by others."""
    item_id: str
    content: str
    source_agent: str
    tags: list[str]
    priority: str = "normal"        # "low" | "normal" | "high"
    ttl_seconds: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedbackEntry:
    """A single relevance judgment, used to learn source weights."""
    query: str
    source: str
    chunk_id: str
    relevant: bool
    timestamp: Optional[str] = None   # ISO-8601 string
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Anything that can embed a string into a float vector."""
    def embed(self, text: str) -> list[float]: ...


@runtime_checkable
class StorageBackend(Protocol):
    """Minimal key-value storage interface."""
    def get(self, key: str) -> Any: ...
    def put(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> None: ...
