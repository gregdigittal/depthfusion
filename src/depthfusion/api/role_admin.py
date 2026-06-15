"""REST admin endpoints for RBAC role management.

T-558: POST /v2/admin/roles — requires owner capability (ASSIGN_ROLES).

Routes
------
POST /v2/admin/roles
    Assign a role to a principal.
    Body: ``{"principal_id": str, "role": str, "action": "assign" | "revoke"}``
    Requires: ``Capability.ASSIGN_ROLES``

GET /v2/admin/roles
    List all role assignments.
    Requires: ``Capability.VIEW_AUDIT_LOG``

All write operations emit an audit event to
``$DEPTHFUSION_DATA_DIR/audit.jsonl`` (same file used by the CLI).

Audit event shape (JSONL)::

    {
        "ts": "<ISO-8601>",
        "action": "role_assigned" | "role_revoked",
        "principal_id": "<target>",
        "role": "<role value>",
        "actor": "<acting principal_id>",
        "result": "ok" | "not_found"
    }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from depthfusion.api.auth import require_principal
from depthfusion.authz import get_policy_engine
from depthfusion.authz.roles import Capability, Role, RoleStore
from depthfusion.identity.models import Principal

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v2/admin", tags=["role-admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    data_dir = Path(
        os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
    ).expanduser()
    return data_dir / "identity.db"


def _default_audit_path() -> Path:
    data_dir = Path(
        os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
    ).expanduser()
    return data_dir / "audit.jsonl"


def _get_role_store() -> RoleStore:
    """Dependency: create a RoleStore from env-configured DB path."""
    return RoleStore(_default_db_path())


def _write_audit_event(
    action: str,
    principal_id: str,
    role: str,
    actor: str,
    result: str,
) -> None:
    """Append an audit event to the JSONL audit log."""
    path = _default_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "principal_id": principal_id,
        "role": role,
        "actor": actor,
        "result": result,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")

    log.info(
        "audit",
        action=action,
        principal_id=principal_id,
        role=role,
        actor=actor,
        result=result,
    )


def _enforce(principal: Principal, capability: Capability) -> None:
    """Raise 403 if *principal* is denied *capability* by the PolicyEngine."""
    decision = get_policy_engine().decide(
        principal,
        capability,
        {"acl_allow": [principal.principal_id]},
    )
    if not decision.allow:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "detail": decision.reason},
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RoleAssignmentBody(BaseModel):
    """Request body for POST /v2/admin/roles."""

    principal_id: str
    role: str
    action: Literal["assign", "revoke"] = "assign"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        valid = {r.value for r in Role}
        if v.lower() not in valid:
            raise ValueError(f"Invalid role '{v}'. Valid roles: {', '.join(sorted(valid))}")
        return v.lower()


class RoleAssignmentResponse(BaseModel):
    """Response body for POST /v2/admin/roles."""

    principal_id: str
    role: str
    action: str
    result: str


class RoleListItem(BaseModel):
    """Single row in GET /v2/admin/roles response."""

    principal_id: str
    role: str
    granted_by: str
    granted_at: float


class RoleListResponse(BaseModel):
    """Response body for GET /v2/admin/roles."""

    assignments: list[RoleListItem]
    count: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/roles", response_model=RoleAssignmentResponse)
async def manage_role(
    body: RoleAssignmentBody,
    principal: Annotated[Principal, Depends(require_principal)],
    store: Annotated[RoleStore, Depends(_get_role_store)],
) -> RoleAssignmentResponse:
    """Assign or revoke a role for a principal.

    Requires the ``assign_roles`` capability (owner-only).

    Body
    ----
    principal_id : str
        The principal receiving / losing the role.
    role : str
        One of ``owner``, ``admin``, ``member``, ``viewer``.
    action : ``"assign"`` | ``"revoke"``
        Whether to grant or remove the role (default: ``"assign"``).
    """
    _enforce(principal, Capability.ASSIGN_ROLES)

    role = Role(body.role)
    actor = principal.principal_id

    if body.action == "assign":
        store.grant(body.principal_id, role, granted_by=actor)
        result = "ok"
        _write_audit_event(
            action="role_assigned",
            principal_id=body.principal_id,
            role=role.value,
            actor=actor,
            result=result,
        )
    else:
        removed = store.revoke(body.principal_id, role)
        result = "ok" if removed else "not_found"
        _write_audit_event(
            action="role_revoked",
            principal_id=body.principal_id,
            role=role.value,
            actor=actor,
            result=result,
        )

    # Flush stale policy decisions for the affected principal so that the
    # new role takes effect immediately (not after the 60s cache TTL).
    get_policy_engine().invalidate(body.principal_id)

    return RoleAssignmentResponse(
        principal_id=body.principal_id,
        role=role.value,
        action=body.action,
        result=result,
    )


@router.get("/roles", response_model=RoleListResponse)
async def list_roles(
    principal: Annotated[Principal, Depends(require_principal)],
    store: Annotated[RoleStore, Depends(_get_role_store)],
) -> RoleListResponse:
    """List all role assignments.

    Requires the ``view_audit_log`` capability (admin or owner).
    """
    _enforce(principal, Capability.VIEW_AUDIT_LOG)

    assignments = store.list_assignments()
    items = [
        RoleListItem(
            principal_id=a["principal_id"],
            role=a["role"],
            granted_by=a["granted_by"],
            granted_at=a["granted_at"],
        )
        for a in assignments
    ]
    return RoleListResponse(assignments=items, count=len(items))


__all__ = ["router", "RoleAssignmentBody", "RoleAssignmentResponse", "RoleListResponse"]
