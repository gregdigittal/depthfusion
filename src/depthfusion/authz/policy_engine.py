"""DepthFusion V2 — Central Policy Engine (T-568).

``PolicyEngine`` is the single authorization decision point.  It wraps the
lower-level RBAC capability check, ACL membership verification, and
classification-level policy into one ``decide()`` call, and caches decisions
with a configurable TTL to reduce hot-path overhead.

Usage
-----
::

    from depthfusion.authz import PolicyEngine, PolicyDecision

    engine = PolicyEngine()
    decision = engine.decide(
        principal,
        action="read_shared_records",
        resource={"acl_allow": ["alice"], "classification": "internal"},
    )
    if not decision.allow:
        raise HTTPException(status_code=403, detail="forbidden")

Thread safety
-------------
``PolicyEngine`` and ``_DecisionCache`` are fully thread-safe.  The engine is
safe to share as a module-level singleton.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Union

import structlog

from depthfusion.authz.capability_check import _capabilities_for_principal
from depthfusion.authz.classification import (
    CLASSIFICATION_POLICY,
    ClassificationLevel,
)
from depthfusion.authz.roles import Capability
from depthfusion.identity.models import Principal

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDecision:
    """Typed outcome of a single policy decision.

    Attributes
    ----------
    allow:
        True if access is granted.
    reason:
        Human-readable explanation.  On deny this names the failing check.
        **Do not surface this string to end-users in production.**
    capability:
        The ``Capability`` evaluated, if any.
    """

    allow: bool
    reason: str
    capability: Capability | None = None


# ---------------------------------------------------------------------------
# Admin-override capability sets
# ---------------------------------------------------------------------------

# READ-class: READ_ALL_RECORDS grants an ACL bypass on these capabilities.
_READ_CAPS: frozenset[Capability] = frozenset(
    {
        Capability.READ_OWN_RECORDS,
        Capability.READ_SHARED_RECORDS,
        Capability.READ_ALL_RECORDS,
        Capability.READ_RESTRICTED,
        Capability.VIEW_AUDIT_LOG,
    }
)

# WRITE-class: WRITE_ALL_RECORDS grants an ACL bypass on these capabilities.
_WRITE_CAPS: frozenset[Capability] = frozenset(
    {
        Capability.CREATE_OWN_RECORDS,
        Capability.WRITE_OWN_RECORDS,
        Capability.WRITE_ALL_RECORDS,
    }
)

# ---------------------------------------------------------------------------
# TTL decision cache
# ---------------------------------------------------------------------------

_DEFAULT_TTL_SECONDS: float = 60.0
_DEFAULT_MAX_SIZE: int = 4096


class _DecisionCache:
    """Thread-safe TTL cache for ``PolicyDecision`` objects.

    Entries expire after ``ttl`` seconds.  Old entries are evicted lazily on
    lookup and eagerly (oldest fraction) when the cache reaches ``max_size``.
    """

    def __init__(
        self, ttl: float = _DEFAULT_TTL_SECONDS, max_size: int = _DEFAULT_MAX_SIZE
    ) -> None:
        self._ttl = ttl
        self._max_size = max_size
        # value: (decision, expires_at_monotonic)
        self._store: dict[tuple, tuple[PolicyDecision, float]] = {}
        self._lock = threading.RLock()

    def get(self, key: tuple) -> PolicyDecision | None:
        """Return the cached decision for *key*, or None if absent / expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            decision, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return decision

    def put(self, key: tuple, decision: PolicyDecision) -> None:
        """Store *decision* under *key*.  Evicts stale/overflow entries first."""
        with self._lock:
            if len(self._store) >= self._max_size:
                now = time.monotonic()
                # Remove expired entries first.
                expired = [k for k, (_, exp) in self._store.items() if exp <= now]
                for k in expired:
                    del self._store[k]
                # If still over limit, remove oldest entries (by expiry).
                if len(self._store) >= self._max_size:
                    overflow = len(self._store) - self._max_size + 1
                    oldest = sorted(self._store, key=lambda k: self._store[k][1])
                    for k in oldest[:overflow]:
                        del self._store[k]
            self._store[key] = (decision, time.monotonic() + self._ttl)

    def invalidate_principal(self, principal_id: str) -> int:
        """Remove all cached decisions for *principal_id*.

        Call after role grants/revocations or ACL changes to prevent stale
        allow/deny decisions from being served.

        Returns
        -------
        int
            Number of entries removed.
        """
        with self._lock:
            keys = [k for k in self._store if k[0] == principal_id]
            for k in keys:
                del self._store[k]
            return len(keys)

    def clear(self) -> None:
        """Flush all entries from the cache."""
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        """Current number of entries (including not-yet-expired ones)."""
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """Central authorization decision point for DepthFusion V2.

    Evaluation order (fail-fast):

    1. **Action resolution** — unknown action string → deny immediately.
    2. **RBAC check** — principal's role set must grant the requested
       ``Capability``.
    3. **ACL check** — principal must appear in ``resource["acl_allow"]``,
       OR hold an admin-override capability (``READ_ALL_RECORDS`` /
       ``WRITE_ALL_RECORDS``).
    4. **Classification check** — if ``resource["classification"]`` is
       present, the principal must hold a role permitted at that tier by
       ``CLASSIFICATION_POLICY``.

    Parameters
    ----------
    cache_ttl:
        Decision cache TTL in seconds.  Defaults to 60 s.
    cache_max_size:
        Maximum decision cache size.  Defaults to 4096 entries.
    """

    def __init__(
        self,
        *,
        cache_ttl: float = _DEFAULT_TTL_SECONDS,
        cache_max_size: int = _DEFAULT_MAX_SIZE,
    ) -> None:
        self._cache = _DecisionCache(ttl=cache_ttl, max_size=cache_max_size)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decide(
        self,
        principal: Principal,
        action: Union[str, Capability],
        resource: dict,
    ) -> PolicyDecision:
        """Evaluate whether *principal* may perform *action* on *resource*.

        Parameters
        ----------
        principal:
            The authenticated caller.
        action:
            The requested capability — a ``Capability`` enum value or its
            string equivalent (e.g. ``"read_shared_records"``).
        resource:
            Dict describing the resource.  Recognised keys:

            ``acl_allow`` (``list[str]``)
                Principal IDs explicitly allowed on the resource.

            ``classification`` (``str``)
                Optional classification label.  If present it is matched
                against ``ClassificationLevel`` values; unknown labels deny.

        Returns
        -------
        PolicyDecision
            Never raises — always returns a decision.
        """
        # Step 1 — resolve action to a Capability
        capability = _resolve_capability(action)
        if capability is None:
            decision = PolicyDecision(
                allow=False,
                reason=f"Unknown action '{action}' — deny by default.",
                capability=None,
            )
            log.warning(
                "policy.unknown_action",
                principal_id=principal.principal_id,
                action=str(action),
            )
            return decision

        # Step 2 — cache lookup (skip evaluation if hit)
        cache_key = _make_cache_key(principal, capability, resource)
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.debug(
                "policy.cache_hit",
                principal_id=principal.principal_id,
                capability=capability.value,
                allow=cached.allow,
            )
            return cached

        # Step 3 — evaluate (miss)
        decision = self._evaluate(principal, capability, resource)
        self._cache.put(cache_key, decision)

        log.info(
            "policy.decision",
            principal_id=principal.principal_id,
            capability=capability.value,
            allow=decision.allow,
            reason=decision.reason,
        )
        return decision

    def invalidate(self, principal_id: str) -> int:
        """Invalidate all cached decisions for *principal_id*.

        Must be called after any role or ACL change that affects the
        principal, to prevent stale decisions from being served until TTL
        expiry.

        Returns
        -------
        int
            Number of cache entries removed.
        """
        evicted = self._cache.invalidate_principal(principal_id)
        if evicted:
            log.info(
                "policy.cache_invalidated",
                principal_id=principal_id,
                evicted=evicted,
            )
        return evicted

    def clear_cache(self) -> None:
        """Flush the entire decision cache."""
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Current number of entries in the decision cache."""
        return self._cache.size

    # ------------------------------------------------------------------
    # Evaluation logic (no cache I/O)
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        principal: Principal,
        capability: Capability,
        resource: dict,
    ) -> PolicyDecision:
        """Run all policy checks for the given (principal, capability, resource).

        Does not touch the cache.
        """
        # RBAC check
        caps = _capabilities_for_principal(principal)
        if capability not in caps:
            return PolicyDecision(
                allow=False,
                reason=(
                    f"Principal '{principal.principal_id}' does not hold "
                    f"capability '{capability.value}'."
                ),
                capability=capability,
            )

        # ACL check (with admin-override)
        acl_allow: list[str] = resource.get("acl_allow") or []
        in_acl = principal.principal_id in acl_allow
        if not in_acl:
            has_read_all = Capability.READ_ALL_RECORDS in caps
            has_write_all = Capability.WRITE_ALL_RECORDS in caps
            if capability in _READ_CAPS and has_read_all:
                in_acl = True
            elif capability in _WRITE_CAPS and has_write_all:
                in_acl = True

        if not in_acl:
            return PolicyDecision(
                allow=False,
                reason=(
                    f"Principal '{principal.principal_id}' is not in the "
                    f"resource ACL."
                ),
                capability=capability,
            )

        # Classification check
        raw_cls = resource.get("classification")
        if raw_cls is not None:
            cls_decision = _check_classification(principal, capability, raw_cls)
            if cls_decision is not None:
                return cls_decision

        return PolicyDecision(
            allow=True,
            reason=f"Access granted: capability '{capability.value}' verified.",
            capability=capability,
        )


# ---------------------------------------------------------------------------
# Module-level singleton (convenience; callers may also instantiate directly)
# ---------------------------------------------------------------------------

_default_engine: PolicyEngine | None = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    """Return the process-wide default ``PolicyEngine`` instance.

    The engine is created lazily on first call (thread-safe).  Use this to
    avoid constructing multiple engines in application code.
    """
    global _default_engine
    if _default_engine is None:
        with _engine_lock:
            if _default_engine is None:
                _default_engine = PolicyEngine()
    return _default_engine


# ---------------------------------------------------------------------------
# Pure helpers (no state)
# ---------------------------------------------------------------------------


def _resolve_capability(action: Union[str, Capability]) -> Capability | None:
    """Coerce *action* to a ``Capability``, or return None for unknown strings."""
    if isinstance(action, Capability):
        return action
    try:
        return Capability(action)
    except ValueError:
        return None


def _make_cache_key(
    principal: Principal,
    capability: Capability,
    resource: dict,
) -> tuple:
    """Build a hashable cache key from the decision inputs."""
    acl = tuple(sorted(resource.get("acl_allow") or []))
    classification = resource.get("classification") or ""
    return (principal.principal_id, capability.value, acl, classification)


def _check_classification(
    principal: Principal,
    capability: Capability,
    raw_cls: str,
) -> PolicyDecision | None:
    """Return a deny decision if the principal cannot access *raw_cls* data.

    Returns None if the check passes (caller should continue evaluation).
    """
    try:
        level = ClassificationLevel(str(raw_cls))
    except ValueError:
        return PolicyDecision(
            allow=False,
            reason=(
                f"Unknown classification label '{raw_cls}' — deny by default."
            ),
            capability=capability,
        )

    policy = CLASSIFICATION_POLICY.get(level)
    if policy is None:
        return PolicyDecision(
            allow=False,
            reason=f"No policy defined for classification '{level.value}' — deny.",
            capability=capability,
        )

    # Compare principal groups against classification-policy Role values.
    allowed_role_values = {r.value for r in policy["allowed_roles"]}
    principal_roles = set(principal.groups)
    if not principal_roles & allowed_role_values:
        return PolicyDecision(
            allow=False,
            reason=(
                f"Principal has no role permitted for '{level.value}' "
                f"classified data."
            ),
            capability=capability,
        )

    return None


__all__ = [
    "PolicyDecision",
    "PolicyEngine",
    "get_policy_engine",
]
