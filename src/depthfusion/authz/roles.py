"""DepthFusion V2 — RBAC Role and Capability schema.

T-556: Role/capability schema + migration

Defines 4 canonical roles (owner, admin, member, viewer) with an extensible
Capability enum and ROLE_CAPABILITIES mapping.

The roles table migration (0002_roles.sql) adds role assignments to the
principal_store SQLite database.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------


class Capability(str, Enum):
    """Fine-grained capabilities that can be granted through a role.

    Names follow the pattern ``<verb>_<noun>`` to keep the matrix readable.
    New capabilities can be added here without touching the Role or
    ROLE_CAPABILITIES definitions — just extend both as needed.
    """

    # ── Record operations ──────────────────────────────────────────────────
    CREATE_OWN_RECORDS = "create_own_records"
    """Create records owned by the principal (used by member/owner)."""

    READ_OWN_RECORDS = "read_own_records"
    """Read records owned by the principal."""

    READ_SHARED_RECORDS = "read_shared_records"
    """Read records shared with the principal (in acl_allow but not owner)."""

    READ_ALL_RECORDS = "read_all_records"
    """Read any record regardless of acl_allow (admin/owner override)."""

    READ_RESTRICTED = "read_restricted"
    """Read records with classification=restricted (elevated privilege)."""

    WRITE_OWN_RECORDS = "write_own_records"
    """Update/delete records owned by the principal."""

    WRITE_ALL_RECORDS = "write_all_records"
    """Update/delete any record (admin/owner override)."""

    # ── User / device management ───────────────────────────────────────────
    MANAGE_USERS = "manage_users"
    """Create, update, delete principal accounts."""

    MANAGE_DEVICES = "manage_devices"
    """Register, revoke, and inspect device leases."""

    # ── Settings / configuration ───────────────────────────────────────────
    MANAGE_SETTINGS = "manage_settings"
    """Change system-wide and project-level configuration."""

    # ── Audit / observability ──────────────────────────────────────────────
    VIEW_AUDIT_LOG = "view_audit_log"
    """Read the audit/event log across all principals."""

    # ── Role administration ────────────────────────────────────────────────
    ASSIGN_ROLES = "assign_roles"
    """Grant or revoke roles on behalf of other principals."""

    REVOKE_ROLES = "revoke_roles"
    """Revoke role assignments (subset of assign_roles for least-privilege)."""


# ---------------------------------------------------------------------------
# Role enum
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """Canonical RBAC roles for DepthFusion V2.

    Four roles are defined in ascending privilege order:

    viewer  < member  < admin  < owner

    The ordering is semantic only; enforcement is via ROLE_CAPABILITIES.
    """

    OWNER = "owner"
    """Full access — all capabilities.  Intended for the deployment owner."""

    ADMIN = "admin"
    """Manage users/devices/settings and view all records.  Cannot grant owner."""

    MEMBER = "member"
    """Create and read own records; read shared records.  Default for team members."""

    VIEWER = "viewer"
    """Read-only access to records shared with them.  No write, no admin."""


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------

ROLE_CAPABILITIES: dict[Role, set[Capability]] = {
    Role.VIEWER: {
        Capability.READ_OWN_RECORDS,
        Capability.READ_SHARED_RECORDS,
    },
    Role.MEMBER: {
        Capability.CREATE_OWN_RECORDS,
        Capability.READ_OWN_RECORDS,
        Capability.READ_SHARED_RECORDS,
        Capability.WRITE_OWN_RECORDS,
    },
    Role.ADMIN: {
        Capability.CREATE_OWN_RECORDS,
        Capability.READ_OWN_RECORDS,
        Capability.READ_SHARED_RECORDS,
        Capability.READ_ALL_RECORDS,
        Capability.WRITE_OWN_RECORDS,
        Capability.MANAGE_USERS,
        Capability.MANAGE_DEVICES,
        Capability.MANAGE_SETTINGS,
        Capability.VIEW_AUDIT_LOG,
    },
    Role.OWNER: set(Capability),  # all capabilities
}


def has_capability(role: Role, capability: Capability) -> bool:
    """Return True if *role* grants *capability*.

    Parameters
    ----------
    role:
        The role to check.
    capability:
        The capability being queried.
    """
    return capability in ROLE_CAPABILITIES[role]


# ---------------------------------------------------------------------------
# roles table DDL (applied to principal_store DB via migration 0002)
# ---------------------------------------------------------------------------

_ROLES_DDL = """
CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id TEXT NOT NULL,
    role        TEXT NOT NULL,
    granted_by  TEXT NOT NULL,
    granted_at  REAL NOT NULL,
    UNIQUE (principal_id, role)
);
CREATE INDEX IF NOT EXISTS idx_roles_principal_id ON roles(principal_id);
"""


# ---------------------------------------------------------------------------
# RoleStore — thin SQLite wrapper for role assignments
# ---------------------------------------------------------------------------


class RoleStore:
    """Persist role assignments in the principal_store SQLite database.

    Each public method opens and closes its own connection under an RLock
    (same pattern as PrincipalStore).

    Parameters
    ----------
    db_path:
        Path to the SQLite file shared with PrincipalStore.  Defaults to
        ``$DEPTHFUSION_DATA_DIR/identity.db`` (or ``~/.depthfusion/identity.db``).
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        import os

        if db_path is None:
            data_dir = Path(
                os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
            ).expanduser()
            self._db_path = data_dir / "identity.db"
        else:
            self._db_path = Path(db_path)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with closing(self._connect()) as conn:
                conn.executescript(_ROLES_DDL)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def grant(self, principal_id: str, role: Role, granted_by: str) -> None:
        """Assign *role* to *principal_id*.

        Idempotent — re-granting an existing assignment does not raise.

        Parameters
        ----------
        principal_id:
            The principal receiving the role.
        role:
            The Role to grant.
        granted_by:
            The principal_id of the actor performing the grant (for audit).
        """
        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO roles
                        (principal_id, role, granted_by, granted_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (principal_id, role.value, granted_by, time.time()),
                )
                conn.commit()

    def revoke(self, principal_id: str, role: Role) -> bool:
        """Remove *role* from *principal_id*.

        Parameters
        ----------
        principal_id:
            The principal whose role is being revoked.
        role:
            The Role to remove.

        Returns
        -------
        bool
            True if a row was deleted, False if the assignment did not exist.
        """
        with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    "DELETE FROM roles WHERE principal_id = ? AND role = ?",
                    (principal_id, role.value),
                )
                conn.commit()
                return cur.rowcount > 0

    def get_roles(self, principal_id: str) -> list[Role]:
        """Return all roles assigned to *principal_id*.

        Parameters
        ----------
        principal_id:
            The principal to look up.

        Returns
        -------
        list[Role]
            Possibly empty list of Role values.
        """
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT role FROM roles WHERE principal_id = ?",
                    (principal_id,),
                ).fetchall()
        return [Role(r["role"]) for r in rows]

    def get_capabilities(self, principal_id: str) -> set[Capability]:
        """Return the union of capabilities across all roles held by *principal_id*.

        Parameters
        ----------
        principal_id:
            The principal to look up.

        Returns
        -------
        set[Capability]
            The merged capability set (empty if no roles are assigned).
        """
        caps: set[Capability] = set()
        for role in self.get_roles(principal_id):
            caps |= ROLE_CAPABILITIES[role]
        return caps

    def list_assignments(self) -> list[dict]:
        """Return all role assignments as a list of dicts.

        Each dict contains: principal_id, role, granted_by, granted_at.
        """
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT principal_id, role, granted_by, granted_at FROM roles "
                    "ORDER BY granted_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]


__all__ = [
    "Capability",
    "Role",
    "ROLE_CAPABILITIES",
    "RoleStore",
    "has_capability",
]
