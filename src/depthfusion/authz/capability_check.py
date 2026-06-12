"""DepthFusion V2 — Capability-check service.

T-557: Implements ``require_capability`` — the single enforcement point
that combines RBAC capability checks with ACL membership.

A call succeeds (returns None) only when **both** conditions hold:

1. The principal's role set grants the requested capability.
2. The principal's ID appears in the resource's ``acl_allow`` list
   (or the capability implies access beyond the ACL, as determined by
   the caller — for now, both conditions must independently pass).

Any failure raises :class:`AuthorizationError`.

Integration
-----------
:func:`make_require_principal` in :mod:`depthfusion.identity.fastapi_deps`
is extended to accept an optional ``capability=`` keyword so routes can
gate on a capability as well as authentication::

    from depthfusion.identity.fastapi_deps import make_require_principal
    from depthfusion.authz.roles import Capability

    require_principal = make_require_principal(validator)
    require_editor = require_principal(capability=Capability.WRITE_OWN_RECORDS)

    @app.put("/records/{record_id}")
    async def update_record(
        record_id: str,
        principal: Annotated[Principal, Depends(require_editor)],
    ):
        ...
"""
from __future__ import annotations

import structlog

from depthfusion.authz.roles import ROLE_CAPABILITIES, Capability, Role
from depthfusion.identity.models import Principal

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class AuthorizationError(Exception):
    """Raised when a principal is denied access to a resource or capability.

    Attributes
    ----------
    principal_id:
        The principal that was denied.
    capability:
        The capability that was checked (may be None for ACL-only denials).
    reason:
        Human-readable explanation suitable for logging (not exposed to
        end-users in production — callers should surface a generic 403).
    """

    def __init__(
        self,
        principal_id: str,
        reason: str,
        capability: Capability | None = None,
    ) -> None:
        super().__init__(reason)
        self.principal_id = principal_id
        self.capability = capability
        self.reason = reason

    def __repr__(self) -> str:
        return (
            f"AuthorizationError(principal_id={self.principal_id!r}, "
            f"capability={self.capability!r}, reason={self.reason!r})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capabilities_for_principal(principal: Principal) -> set[Capability]:
    """Derive the capability set from the roles embedded in the principal.

    The principal carries a ``groups`` list.  Role assignments may be
    represented as group names matching Role enum values (e.g. ``"owner"``,
    ``"admin"``).  Unknown group names are silently skipped.

    If the principal has no recognised role groups the returned set is empty,
    meaning all capability checks will fail.

    Parameters
    ----------
    principal:
        The authenticated caller.

    Returns
    -------
    set[Capability]
        Union of capabilities from all matched roles.
    """
    caps: set[Capability] = set()
    role_values = {r.value for r in Role}
    for group in principal.groups:
        if group in role_values:
            caps |= ROLE_CAPABILITIES[Role(group)]
    return caps


# ---------------------------------------------------------------------------
# Core enforcement function
# ---------------------------------------------------------------------------


def require_capability(
    principal: Principal,
    capability: Capability,
    resource_acl: list[str],
) -> None:
    """Assert that *principal* may exercise *capability* on a resource.

    Two independent checks must both pass:

    1. **Capability check** — the principal's roles grant *capability*.
    2. **ACL check** — ``principal.principal_id`` is listed in
       *resource_acl*.

    Parameters
    ----------
    principal:
        The authenticated caller.
    capability:
        The capability being requested (e.g. ``Capability.WRITE_OWN_RECORDS``).
    resource_acl:
        List of principal IDs allowed to access the resource.  An empty
        list means no principal is allowed (resource is locked down).

    Raises
    ------
    AuthorizationError
        If the principal lacks the requested capability OR is not in the
        resource's ACL.  The error message distinguishes the two cases to
        aid debugging without leaking ACL contents to end-users.
    """
    caps = _capabilities_for_principal(principal)

    if capability not in caps:
        log.warning(
            "authz.capability_denied",
            principal_id=principal.principal_id,
            capability=capability.value,
            held_capabilities=[c.value for c in caps],
        )
        raise AuthorizationError(
            principal_id=principal.principal_id,
            capability=capability,
            reason=(
                f"Principal '{principal.principal_id}' does not hold capability "
                f"'{capability.value}' — check role assignment."
            ),
        )

    if principal.principal_id not in resource_acl:
        log.warning(
            "authz.acl_denied",
            principal_id=principal.principal_id,
            capability=capability.value,
        )
        raise AuthorizationError(
            principal_id=principal.principal_id,
            capability=capability,
            reason=(
                f"Principal '{principal.principal_id}' is not in the resource ACL."
            ),
        )

    log.debug(
        "authz.access_granted",
        principal_id=principal.principal_id,
        capability=capability.value,
    )


__all__ = [
    "AuthorizationError",
    "require_capability",
]
