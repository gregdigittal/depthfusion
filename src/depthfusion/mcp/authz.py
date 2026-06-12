"""DepthFusion MCP authorization — T-578/T-579.

Principal binding and per-tool capability annotations for MCP tool dispatch.

Every MCP tool is annotated with the minimum Capability required to call it.
``_dispatch_tool`` enforces the annotation by checking the bound principal
before delegating to the implementation function.

Design
------
* ``TOOL_CAPABILITIES``: dict mapping tool name → required Capability.
* ``requires_capability`` decorator: records the required capability on a
  function as ``_required_capability``. Used as documentation and for
  tooling — the enforcement happens in ``_dispatch_tool``.
* ``check_tool_access``: the runtime enforcement function called from
  ``_dispatch_tool``. Raises ``AuthorizationError`` if the principal lacks
  the annotation, or if ``principal`` is ``None`` (unauthenticated call).

The global open ACL sentinel ``_GLOBAL_ACL`` is used so that the
capability check does not require a per-record ACL when the tool is
invoked — the tool itself is the resource, and access is governed purely
by capability membership.
"""
from __future__ import annotations

import functools
from typing import Any, Callable

from depthfusion.authz import get_policy_engine
from depthfusion.authz.capability_check import AuthorizationError
from depthfusion.authz.roles import Capability
from depthfusion.identity.models import Principal

# ---------------------------------------------------------------------------
# Sentinel — tool-level ACL that admits any principal in the ACL check.
# We use the principal_id itself so the ACL check always passes once the
# capability check has passed.
# ---------------------------------------------------------------------------

_TOOL_ACL_OPEN = "__open__"  # placeholder; replaced dynamically per call


def _open_acl_for(principal_id: str) -> list[str]:
    """Return a single-entry ACL that admits exactly the given principal.

    When enforcing a tool-level capability (rather than a per-record ACL),
    we still need to pass a non-empty ACL list to ``require_capability``.
    This helper constructs one that admits the caller without any DB lookup.
    """
    return [principal_id]


# ---------------------------------------------------------------------------
# Per-tool capability annotations (T-579)
# ---------------------------------------------------------------------------

# Capability required for each of the 29 MCP tools.
# Mapping: tool_name → Capability
# Read tools use READ_OWN_RECORDS (minimum read privilege).
# Write/capture tools use WRITE_OWN_RECORDS.
# Admin/manage tools use MANAGE_SETTINGS.
# Audit tools use VIEW_AUDIT_LOG.

TOOL_CAPABILITIES: dict[str, Capability] = {
    # ── Status / health (read-only, minimum privilege) ──────────────────
    "depthfusion_status": Capability.READ_OWN_RECORDS,
    "depthfusion_list_providers": Capability.READ_OWN_RECORDS,
    # ── Recall / retrieval (read) ────────────────────────────────────────
    "depthfusion_recall_relevant": Capability.READ_OWN_RECORDS,
    "depthfusion_retrieve_context": Capability.READ_OWN_RECORDS,
    "depthfusion_session_seed": Capability.READ_OWN_RECORDS,
    "depthfusion_graph_traverse": Capability.READ_OWN_RECORDS,
    "depthfusion_graph_status": Capability.READ_OWN_RECORDS,
    # ── Capture / write ──────────────────────────────────────────────────
    "depthfusion_tag_session": Capability.WRITE_OWN_RECORDS,
    "depthfusion_publish_context": Capability.WRITE_OWN_RECORDS,
    "depthfusion_auto_learn": Capability.WRITE_OWN_RECORDS,
    "depthfusion_compress_session": Capability.WRITE_OWN_RECORDS,
    "depthfusion_confirm_discovery": Capability.WRITE_OWN_RECORDS,
    "depthfusion_set_memory_score": Capability.WRITE_OWN_RECORDS,
    "depthfusion_recall_feedback": Capability.WRITE_OWN_RECORDS,
    "depthfusion_pin_discovery": Capability.WRITE_OWN_RECORDS,
    "depthfusion_ingest_conversation": Capability.WRITE_OWN_RECORDS,
    # ── Decisions / knowledge management ────────────────────────────────
    "depthfusion_record_decision": Capability.WRITE_OWN_RECORDS,
    "depthfusion_record_incident": Capability.WRITE_OWN_RECORDS,
    "depthfusion_mark_superseded": Capability.WRITE_OWN_RECORDS,
    "depthfusion_report_outcome": Capability.WRITE_OWN_RECORDS,
    # ── Scope management (write-own level) ──────────────────────────────
    "depthfusion_set_scope": Capability.WRITE_OWN_RECORDS,
    # ── Telemetry ────────────────────────────────────────────────────────
    "depthfusion_record_telemetry": Capability.WRITE_OWN_RECORDS,
    "depthfusion_query_telemetry": Capability.VIEW_AUDIT_LOG,
    # ── Project management ───────────────────────────────────────────────
    "depthfusion_register_project": Capability.MANAGE_SETTINGS,
    "depthfusion_list_projects": Capability.READ_OWN_RECORDS,
    "depthfusion_sync_project": Capability.MANAGE_SETTINGS,
    "depthfusion_ingest_project": Capability.MANAGE_SETTINGS,
    # ── Research / bridge ────────────────────────────────────────────────
    "depthfusion_research_topic": Capability.WRITE_OWN_RECORDS,
    "depthfusion_bridge": Capability.WRITE_OWN_RECORDS,
}


# ---------------------------------------------------------------------------
# Decorator (T-579 annotation helper)
# ---------------------------------------------------------------------------


def requires_capability(capability: Capability) -> Callable:
    """Mark a tool implementation function with its required capability.

    Usage::

        @requires_capability(Capability.READ_OWN_RECORDS)
        def _tool_recall(arguments: dict) -> str:
            ...

    The decorator is purely documentary at the function level — runtime
    enforcement is performed by ``check_tool_access`` in ``_dispatch_tool``.
    The annotation is stored as ``fn._required_capability`` for introspection
    and testing.
    """

    def decorator(fn: Callable) -> Callable:
        fn._required_capability = capability  # type: ignore[attr-defined]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper._required_capability = capability  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Runtime enforcement
# ---------------------------------------------------------------------------


def check_tool_access(
    tool_name: str,
    principal: Principal | None,
) -> None:
    """Assert that *principal* may call *tool_name*.

    Raises
    ------
    AuthorizationError
        If ``principal`` is ``None`` (unauthenticated) or does not hold the
        required capability for *tool_name*.
    ValueError
        If *tool_name* has no entry in ``TOOL_CAPABILITIES`` (programming
        error — every tool must be annotated).
    """
    if principal is None:
        raise AuthorizationError(
            principal_id="<anonymous>",
            reason=(
                f"Tool '{tool_name}' requires authentication; no principal was "
                "bound to this session."
            ),
        )

    if tool_name not in TOOL_CAPABILITIES:
        raise ValueError(
            f"Tool '{tool_name}' has no capability annotation in TOOL_CAPABILITIES. "
            "Every MCP tool must be annotated — add it to mcp/authz.py."
        )

    required = TOOL_CAPABILITIES[tool_name]
    # Use the principal's own id as the ACL so that the capability check is
    # the only gate (tool-level authz, not per-record authz).
    decision = get_policy_engine().decide(
        principal,
        required,
        {"acl_allow": _open_acl_for(principal.principal_id)},
    )
    if not decision.allow:
        raise AuthorizationError(
            principal_id=principal.principal_id,
            reason=decision.reason,
        )


__all__ = [
    "AuthorizationError",
    "TOOL_CAPABILITIES",
    "check_tool_access",
    "requires_capability",
]
