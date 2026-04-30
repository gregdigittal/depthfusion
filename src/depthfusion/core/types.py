"""Core type definitions for DepthFusion.

Dataclasses for retrieved chunks, session blocks, context items, and feedback,
plus Protocol definitions for pluggable embedding and storage backends.
"""
from __future__ import annotations

import hashlib
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
    """An item published to the context bus by one agent, consumed by others.

    The ``content_hash`` field carries a sha256 over the ``content`` bytes and
    is the dedup key used by ``ContextBus.publish()``. Three constructor
    semantics are supported (S-78):

    * ``content_hash`` omitted (or ``None``) → sha256 of ``content`` is auto-
      derived in ``__post_init__``. This is the path for new callers.
    * ``content_hash=""`` → preserved verbatim as the empty string. Used by
      ``FileBus.subscribe()`` when reconstructing legacy rows that were written
      to ``bus.jsonl`` before this field existed (AC-6 backward-compat).
    * ``content_hash="<hex>"`` → preserved verbatim. Used when an existing
      hash is being threaded through e.g. on JSON round-trip.

    Tags, metadata, priority, ttl, item_id, and source_agent are deliberately
    *not* part of the hash (AC-5): a retry that arrives with different routing
    metadata but the same payload is still a duplicate.
    """
    item_id: str
    content: str
    source_agent: str
    tags: list[str]
    priority: str = "normal"        # "low" | "normal" | "high"
    ttl_seconds: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if self.content_hash is None:
            self.content_hash = hashlib.sha256(
                self.content.encode("utf-8")
            ).hexdigest()


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
