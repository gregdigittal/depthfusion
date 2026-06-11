"""Post-rank ACL verification — T-573.

After any retrieval result set is assembled, run a verification pass to
assert that every returned result is accessible by the requesting principal.

If a result slips through that should have been filtered (e.g., because an
upstream store had a stale acl_cache), it is silently removed here and the
telemetry counter ``acl_leak_prevented`` is incremented.

Design
------
- Checks ``result["acl_allow"]`` (list[str]) against the principal's
  ``principal_id`` and ``groups``.
- A missing / empty ``acl_allow`` is treated as **public** (legacy
  compatibility — pre-V2 documents have no ACL stamp and must remain
  accessible to system callers).
- ``principal=None`` bypasses the check entirely (internal / system calls).
- Fail-open: if the verifier itself raises (e.g., bad data shape), it logs a
  WARNING and returns the original list unchanged rather than crashing the
  recall path.

Telemetry
---------
Leaks are counted via ``MetricsCollector.record()`` under the metric name
``acl_leak_prevented``.  Each leaked result is logged at WARNING level with
the ``record_id`` and ``principal_id`` so operators can trace the upstream
source.

Usage
-----
The verifier is intended to be called as the **last** step before returning
results to the caller::

    from depthfusion.retrieval.acl_verifier import verify_acl

    results = assemble_results(...)
    results = verify_acl(results, principal=principal)
    return results

``record_id`` is resolved from these keys in priority order:
``id``, ``chunk_id``, ``discovery_id``, ``memory_id``, ``<unknown>``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from depthfusion.identity.models import Principal

log = structlog.get_logger(__name__)
_fallback_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RECORD_ID_KEYS = ("id", "chunk_id", "discovery_id", "memory_id")


def _record_id(result: dict[str, Any]) -> str:
    """Extract a stable record identifier from a result dict."""
    for key in _RECORD_ID_KEYS:
        val = result.get(key)
        if val is not None:
            return str(val)
    return "<unknown>"


def _principal_can_access(
    acl_allow: list[str] | None,
    allowed_ids: set[str],
) -> bool:
    """Return True when *allowed_ids* intersects *acl_allow*.

    Rules:
    - ``acl_allow`` is ``None`` or an empty list → **public**, always True.
    - Otherwise at least one entry in ``acl_allow`` must be in ``allowed_ids``.
    """
    if not acl_allow:
        return True
    return bool(set(acl_allow) & allowed_ids)


# ---------------------------------------------------------------------------
# Core verifier
# ---------------------------------------------------------------------------


def verify_acl(
    results: list[dict[str, Any]],
    *,
    principal: "Principal | None",
) -> list[dict[str, Any]]:
    """Post-rank ACL verification pass.

    Parameters
    ----------
    results:
        Ordered retrieval result list. Each element is a dict that MAY
        contain ``acl_allow: list[str]``.
    principal:
        The authenticated caller. ``None`` means an internal / system call
        — all results are returned unchanged.

    Returns
    -------
    list[dict[str, Any]]
        The filtered result list.  Length is ≤ ``len(results)``.
        Each removed record increments the ``acl_leak_prevented`` telemetry
        counter and emits a WARNING log.

    Raises
    ------
    This function never raises.  All internal errors are caught and the
    original result list is returned unchanged (fail-open contract).
    """
    if principal is None:
        return results

    try:
        return _run_verify(results, principal=principal)
    except Exception as exc:  # noqa: BLE001 — fail-open; recall must not crash
        _fallback_logger.warning(
            "acl_verifier: unexpected error — returning unfiltered results: %s",
            exc,
            exc_info=True,
        )
        return results


def _run_verify(
    results: list[dict[str, Any]],
    *,
    principal: "Principal",
) -> list[dict[str, Any]]:
    """Inner implementation — may raise; caller wraps in try/except."""
    allowed_ids: set[str] = {principal.principal_id}
    for group in principal.groups or []:
        allowed_ids.add(group)

    clean: list[dict[str, Any]] = []
    leaked: list[str] = []

    for result in results:
        raw_acl = result.get("acl_allow")

        # Normalise acl_allow: may be stored as JSON string (Chroma stores it
        # this way), a list, or absent.
        acl_allow: list[str] | None = None
        if isinstance(raw_acl, str):
            import json as _json
            try:
                parsed = _json.loads(raw_acl)
                if isinstance(parsed, list):
                    acl_allow = [str(x) for x in parsed]
            except Exception:  # noqa: BLE001
                # Treat unparseable string as a single-entry ACL.
                acl_allow = [raw_acl]
        elif isinstance(raw_acl, list):
            acl_allow = [str(x) for x in raw_acl]
        # else: None / missing → treated as public (see _principal_can_access)

        if _principal_can_access(acl_allow, allowed_ids):
            clean.append(result)
        else:
            rid = _record_id(result)
            leaked.append(rid)
            log.warning(
                "acl_verifier.leak_prevented",
                record_id=rid,
                principal_id=principal.principal_id,
                acl_allow=acl_allow,
            )

    if leaked:
        _emit_leak_counter(len(leaked), principal_id=principal.principal_id)

    return clean


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def _emit_leak_counter(count: int, *, principal_id: str) -> None:
    """Increment ``acl_leak_prevented`` in MetricsCollector."""
    try:
        from depthfusion.metrics.collector import MetricsCollector
        MetricsCollector().record(
            "acl_leak_prevented",
            float(count),
            labels={"principal_id": principal_id},
        )
    except Exception as exc:  # noqa: BLE001 — observability must not block serving
        _fallback_logger.debug(
            "acl_verifier: failed to emit acl_leak_prevented counter: %s", exc
        )


__all__ = ["verify_acl"]
