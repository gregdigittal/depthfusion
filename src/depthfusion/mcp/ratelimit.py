"""MCP rate limiting — RateLimiter Protocol, InMemoryRateLimitBackend, RedisRateLimitBackend.

S-251 / E-73 — fixed-window, per-principal, per-bucket rate limiting for
``POST /mcp``, gated behind the existing ``require_principal`` fail-closed
Bearer auth (no principal → the FastAPI dependency itself rejects with 401
before a request ever reaches the rate limiter, so quota tracking is always
keyed to a real, authenticated principal).

Design — mirrors the ``StreamBackend`` Protocol idiom from
``core/event_store.py``: a Protocol, an ``InMemoryRateLimitBackend`` default
(loopback / single-worker installs), and a ``RedisRateLimitBackend`` for
multi-worker / VPS deployments, raising the same clear install-hint
``ImportError`` when ``redis`` is absent. ``redis>=5.0`` is already an
installable extra (``depthfusion[fabric]``, added for ``RedisStreamBackend``
in E-46) — no new dependency is introduced here.

T-853 deviation (recorded verbatim in BACKLOG.md): fastapi-limiter evaluated
and rejected — redis-only dependency incompatible with core-install default;
implemented RateLimiter Protocol + InMemory/Redis backends per
event_store.py precedent.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults + env overrides (S-251 AC-1 / T-855)
# ---------------------------------------------------------------------------

DEFAULT_RATE_LIMIT_PUBLISH = 60
DEFAULT_RATE_LIMIT_RECALL = 300

_WINDOW_SECONDS = 60

_BUCKET_PUBLISH = "publish"
_BUCKET_RECALL = "recall"


def _resolve_positive_int(env_var: str, default: int) -> int:
    """Read a positive-int env var, degrading to *default* on any bad input.

    Mirrors ``core/event_store.py::checkpoint_ttl_days``'s degrade-safe
    pattern: missing, empty, non-integer, or non-positive values all fall
    back to the default rather than raising or silently disabling the
    limit (a rate limit of 0 or negative would either block every request
    or be meaningless).
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "invalid %s=%r — falling back to default %d", env_var, raw, default,
        )
        return default
    return value if value > 0 else default


def rate_limit_publish_per_minute() -> int:
    """``DEPTHFUSION_RATE_LIMIT_PUBLISH`` — publish calls/minute/principal (default 60)."""
    return _resolve_positive_int("DEPTHFUSION_RATE_LIMIT_PUBLISH", DEFAULT_RATE_LIMIT_PUBLISH)


def rate_limit_recall_per_minute() -> int:
    """``DEPTHFUSION_RATE_LIMIT_RECALL`` — recall calls/minute/principal (default 300)."""
    return _resolve_positive_int("DEPTHFUSION_RATE_LIMIT_RECALL", DEFAULT_RATE_LIMIT_RECALL)


# ---------------------------------------------------------------------------
# RateLimiter Protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a single quota-consuming ``check()`` call."""

    allowed: bool
    retry_after_seconds: int


@runtime_checkable
class RateLimiter(Protocol):
    """Per-principal, per-bucket fixed-window rate limiter.

    Mirrors the ``StreamBackend`` Protocol pattern from
    ``core/event_store.py``. The canonical production implementation is
    ``RedisRateLimitBackend``; ``InMemoryRateLimitBackend`` is the
    loopback / single-worker default. Operators can swap in another
    backend (e.g. a Memcached-backed limiter) without touching the HTTP
    layer by implementing this Protocol.
    """

    async def check(self, principal_id: str, bucket: str, limit: int) -> RateLimitResult:
        """Consume one call from *principal_id*'s *bucket* quota.

        Returns a ``RateLimitResult`` with ``allowed=False`` and
        ``retry_after_seconds > 0`` when *principal_id* has already used
        *limit* calls of *bucket* within the current fixed window.
        """
        ...


# ---------------------------------------------------------------------------
# InMemoryRateLimitBackend — default (loopback / single-worker installs)
# ---------------------------------------------------------------------------

class InMemoryRateLimitBackend:
    """In-process fixed-window ``RateLimiter`` — the default backend.

    Appropriate for the loopback, single-user, single-worker install (the
    project's default deployment profile). NOT safe across multiple
    worker processes — each worker holds its own counters, so the
    effective limit becomes ``limit * worker_count``. See
    ``docs/deployment.md`` for the Redis-backed multi-worker upgrade path
    (S-251 AC-3 / T-856).
    """

    def __init__(self) -> None:
        # key -> (window_start_epoch_seconds, count)
        self._windows: dict[str, tuple[int, int]] = {}

    async def check(self, principal_id: str, bucket: str, limit: int) -> RateLimitResult:
        key = f"{principal_id}:{bucket}"
        now = int(time.time())
        window_start = now - (now % _WINDOW_SECONDS)

        stored_start, count = self._windows.get(key, (window_start, 0))
        if stored_start != window_start:
            stored_start, count = window_start, 0

        count += 1
        self._windows[key] = (stored_start, count)

        if count > limit:
            retry_after = (stored_start + _WINDOW_SECONDS) - now
            return RateLimitResult(allowed=False, retry_after_seconds=max(retry_after, 1))
        return RateLimitResult(allowed=True, retry_after_seconds=0)


# ---------------------------------------------------------------------------
# RedisRateLimitBackend — production, multi-worker (S-251 AC-2)
# ---------------------------------------------------------------------------

class RedisRateLimitBackend:
    """Production ``RateLimiter`` backed by Redis (INCR + EXPIRE fixed window).

    Shared across worker processes via the Redis key namespace
    ``depthfusion:ratelimit:{bucket}:{principal_id}:{window_start}`` — this
    is what makes multi-worker deployments correct (S-251 AC-2/AC-3),
    unlike ``InMemoryRateLimitBackend``.

    Requires ``redis>=5.0`` (``pip install depthfusion[fabric]`` — the same
    extra ``RedisStreamBackend`` in ``core/event_store.py`` requires; no new
    dependency is introduced by this story).
    """

    def __init__(self, redis_url: str) -> None:
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "RedisRateLimitBackend requires redis>=5.0. "
                "Install with: pip install depthfusion[fabric]"
            ) from exc

        self._client = aioredis.from_url(redis_url, decode_responses=True)

    async def check(self, principal_id: str, bucket: str, limit: int) -> RateLimitResult:
        now = int(time.time())
        window_start = now - (now % _WINDOW_SECONDS)
        key = f"depthfusion:ratelimit:{bucket}:{principal_id}:{window_start}"

        count = await self._client.incr(key)
        if count == 1:
            # Only the first request in a fresh window sets the expiry —
            # avoids resetting the TTL on every subsequent INCR.
            await self._client.expire(key, _WINDOW_SECONDS)

        if count > limit:
            ttl = await self._client.ttl(key)
            retry_after = ttl if ttl and ttl > 0 else _WINDOW_SECONDS
            return RateLimitResult(allowed=False, retry_after_seconds=retry_after)
        return RateLimitResult(allowed=True, retry_after_seconds=0)


# ---------------------------------------------------------------------------
# Tool → bucket classification
# ---------------------------------------------------------------------------

def classify_tool_call(tool_name: str) -> tuple[str, int]:
    """Map an MCP tool name to its rate-limit bucket and per-minute limit.

    Read-only tools (annotated ``Capability.READ_OWN_RECORDS`` in
    ``mcp/authz.py::TOOL_CAPABILITIES``) get the more generous "recall"
    bucket (default 300/min). Every other annotated tool — write, admin,
    or audit — gets the tighter "publish" bucket (default 60/min), since
    those are the mutating/expensive operations AC-1 exists to protect.
    An unrecognised tool name is also classified as "publish" — the safer
    (tighter) default when a tool's mutation profile is unknown.
    """
    from depthfusion.authz.roles import Capability
    from depthfusion.mcp.authz import TOOL_CAPABILITIES

    capability = TOOL_CAPABILITIES.get(tool_name)
    if capability == Capability.READ_OWN_RECORDS:
        return _BUCKET_RECALL, rate_limit_recall_per_minute()
    return _BUCKET_PUBLISH, rate_limit_publish_per_minute()


# ---------------------------------------------------------------------------
# Lazy singleton — Redis when DEPTHFUSION_REDIS_URL is set, else InMemory
# ---------------------------------------------------------------------------

_RATE_LIMITER: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Lazy singleton ``RateLimiter`` (S-251 AC-2).

    Redis-backed (shared across worker processes) when
    ``DEPTHFUSION_REDIS_URL`` is set; falls back to the in-process
    ``InMemoryRateLimitBackend`` default when absent — the same
    env-driven backend selection convention as
    ``mcp/tools/_state.py::_get_fabric_store``.
    """
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        redis_url = os.environ.get("DEPTHFUSION_REDIS_URL", "").strip()
        _RATE_LIMITER = (
            RedisRateLimitBackend(redis_url) if redis_url else InMemoryRateLimitBackend()
        )
    return _RATE_LIMITER


def reset_rate_limiter() -> None:
    """Test-only hook — clears the lazy singleton so tests get a fresh limiter."""
    global _RATE_LIMITER
    _RATE_LIMITER = None


__all__ = [
    "DEFAULT_RATE_LIMIT_PUBLISH",
    "DEFAULT_RATE_LIMIT_RECALL",
    "InMemoryRateLimitBackend",
    "RateLimitResult",
    "RateLimiter",
    "RedisRateLimitBackend",
    "classify_tool_call",
    "get_rate_limiter",
    "rate_limit_publish_per_minute",
    "rate_limit_recall_per_minute",
    "reset_rate_limiter",
]
