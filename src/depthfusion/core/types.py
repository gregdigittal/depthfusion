"""Core type definitions for DepthFusion.

Dataclasses for retrieved chunks, session blocks, context items, and feedback,
plus Protocol definitions for pluggable embedding and storage backends.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# E-27 / S-70 — memory policy scoring scalars
# ---------------------------------------------------------------------------

DEFAULT_IMPORTANCE: float = 0.5
"""Canonical default importance for a discovery (∈ [0.0, 1.0])."""

DEFAULT_SALIENCE: float = 1.0
"""Canonical default salience for a discovery (∈ [0.0, 5.0])."""

_IMPORTANCE_MIN, _IMPORTANCE_MAX = 0.0, 1.0
_SALIENCE_MIN, _SALIENCE_MAX = 0.0, 5.0


def _normalize_score(
    value: Optional[float],
    default: float,
    lo: float,
    hi: float,
) -> float:
    """Coerce a score input into a finite, in-range float.

    Contract (S-70 consensus, refined post-Commit-2 review):
      - ``None`` → canonical ``default``
      - non-finite (NaN, +Inf, -Inf) → canonical ``default``
      - finite outside [lo, hi] → clamped to the nearest boundary
      - finite inside [lo, hi] → preserved verbatim

    Accepts only ``Optional[float]`` (or anything Python silently treats
    as a numeric like ``int``). String parsing is the caller's problem —
    ``extract_memory_score`` does that on the parse layer so the type
    contract here stays tight (no ``# type: ignore`` propagation, no
    surprise ``OverflowError`` from custom ``__float__``).
    """
    if value is None:
        return default
    # ``math.isfinite`` accepts int and float; rejects NaN, +Inf, -Inf.
    # If a non-numeric type slipped through, ``isfinite`` raises
    # ``TypeError`` — surface that loudly rather than silent-default,
    # because it indicates a programming bug at the call site.
    if not math.isfinite(value):
        return default
    if value < lo:
        return lo
    if value > hi:
        return hi
    return float(value)


@dataclass
class MemoryScore:
    """Per-discovery scoring scalars (S-70).

    Two independent dimensions:
      * ``importance`` ∈ [0.0, 1.0], default 0.5 — intrinsic value of the
        captured discovery. Set at capture time by extractors (derived from
        their existing ``confidence``) or by an explicit operator override.
      * ``salience`` ∈ [0.0, 5.0], default 1.0 — recent usefulness; mutated
        over time by S-72 recall-feedback signals (separate story).

    Both fields are clamped in ``__post_init__``. Non-finite inputs
    (NaN, ±Inf) collapse to the canonical default for that dimension —
    Python's ``min``/``max`` propagate NaN, so silent passthrough would
    poison the whole policy layer (a NaN ``salience`` always loses
    comparisons against any finite threshold, hard-archiving everything).

    The ``__post_init__`` mirrors S-78's ``ContextItem.content_hash``
    auto-derive idiom for consistency across the core types module.
    """
    importance: Optional[float] = None
    salience: Optional[float] = None

    def __post_init__(self) -> None:
        self.importance = _normalize_score(
            self.importance, DEFAULT_IMPORTANCE,
            _IMPORTANCE_MIN, _IMPORTANCE_MAX,
        )
        self.salience = _normalize_score(
            self.salience, DEFAULT_SALIENCE,
            _SALIENCE_MIN, _SALIENCE_MAX,
        )


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
