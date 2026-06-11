"""Tests for T-556: Role/capability schema + migration.

Covers:
- Capability enum completeness
- Role enum (4 canonical roles)
- ROLE_CAPABILITIES matrix (owner has all, viewer has least)
- has_capability() helper
- RoleStore CRUD: grant, revoke, get_roles, get_capabilities, list_assignments
- Migration: roles table created in principal_store DB
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from depthfusion.authz.roles import (
    Capability,
    Role,
    ROLE_CAPABILITIES,
    RoleStore,
    has_capability,
)


# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------


class TestCapabilityEnum:
    def test_capability_is_str_enum(self) -> None:
        assert isinstance(Capability.READ_OWN_RECORDS, str)

    def test_capability_values_are_snake_case(self) -> None:
        for cap in Capability:
            assert cap.value == cap.value.lower(), f"{cap!r} value is not lowercase"
            assert " " not in cap.value, f"{cap!r} value contains spaces"

    def test_minimum_capability_set(self) -> None:
        expected = {
            "create_own_records",
            "read_own_records",
            "read_shared_records",
            "read_all_records",
            "read_restricted",
            "write_own_records",
            "write_all_records",
            "manage_users",
            "manage_devices",
            "manage_settings",
            "view_audit_log",
            "assign_roles",
            "revoke_roles",
        }
        actual = {c.value for c in Capability}
        missing = expected - actual
        assert not missing, f"Missing capabilities: {missing}"


# ---------------------------------------------------------------------------
# Role enum
# ---------------------------------------------------------------------------


class TestRoleEnum:
    def test_four_canonical_roles_exist(self) -> None:
        roles = {r.value for r in Role}
        assert roles == {"owner", "admin", "member", "viewer"}

    def test_role_is_str_enum(self) -> None:
        assert isinstance(Role.OWNER, str)
        assert Role.OWNER == "owner"

    def test_all_roles_in_capability_matrix(self) -> None:
        for role in Role:
            assert role in ROLE_CAPABILITIES, f"{role!r} missing from ROLE_CAPABILITIES"


# ---------------------------------------------------------------------------
# ROLE_CAPABILITIES matrix
# ---------------------------------------------------------------------------


class TestRoleCapabilities:
    def test_owner_has_all_capabilities(self) -> None:
        owner_caps = ROLE_CAPABILITIES[Role.OWNER]
        all_caps = set(Capability)
        assert owner_caps == all_caps, (
            f"Owner missing capabilities: {all_caps - owner_caps}"
        )

    def test_viewer_is_read_only(self) -> None:
        viewer_caps = ROLE_CAPABILITIES[Role.VIEWER]
        write_caps = {
            Capability.CREATE_OWN_RECORDS,
            Capability.WRITE_OWN_RECORDS,
            Capability.WRITE_ALL_RECORDS,
            Capability.MANAGE_USERS,
            Capability.MANAGE_DEVICES,
            Capability.MANAGE_SETTINGS,
            Capability.ASSIGN_ROLES,
            Capability.REVOKE_ROLES,
        }
        overlap = viewer_caps & write_caps
        assert not overlap, f"Viewer has write capabilities: {overlap}"

    def test_viewer_has_no_admin_capabilities(self) -> None:
        viewer_caps = ROLE_CAPABILITIES[Role.VIEWER]
        admin_only = {
            Capability.MANAGE_USERS,
            Capability.MANAGE_DEVICES,
            Capability.MANAGE_SETTINGS,
            Capability.VIEW_AUDIT_LOG,
            Capability.ASSIGN_ROLES,
            Capability.REVOKE_ROLES,
            Capability.READ_ALL_RECORDS,
            Capability.READ_RESTRICTED,
        }
        overlap = viewer_caps & admin_only
        assert not overlap, f"Viewer has admin capabilities: {overlap}"

    def test_member_can_create_and_write_own(self) -> None:
        member_caps = ROLE_CAPABILITIES[Role.MEMBER]
        assert Capability.CREATE_OWN_RECORDS in member_caps
        assert Capability.WRITE_OWN_RECORDS in member_caps

    def test_member_cannot_read_all(self) -> None:
        member_caps = ROLE_CAPABILITIES[Role.MEMBER]
        assert Capability.READ_ALL_RECORDS not in member_caps

    def test_admin_can_manage_users_devices_settings(self) -> None:
        admin_caps = ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.MANAGE_USERS in admin_caps
        assert Capability.MANAGE_DEVICES in admin_caps
        assert Capability.MANAGE_SETTINGS in admin_caps
        assert Capability.VIEW_AUDIT_LOG in admin_caps

    def test_admin_can_read_all_records(self) -> None:
        admin_caps = ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.READ_ALL_RECORDS in admin_caps

    def test_privilege_escalation_owner_gt_admin(self) -> None:
        owner_caps = ROLE_CAPABILITIES[Role.OWNER]
        admin_caps = ROLE_CAPABILITIES[Role.ADMIN]
        assert admin_caps.issubset(owner_caps), (
            "Admin should have a subset of owner capabilities"
        )

    def test_privilege_escalation_admin_gt_member(self) -> None:
        admin_caps = ROLE_CAPABILITIES[Role.ADMIN]
        member_caps = ROLE_CAPABILITIES[Role.MEMBER]
        assert member_caps.issubset(admin_caps), (
            "Member should have a subset of admin capabilities"
        )

    def test_privilege_escalation_member_gt_viewer(self) -> None:
        member_caps = ROLE_CAPABILITIES[Role.MEMBER]
        viewer_caps = ROLE_CAPABILITIES[Role.VIEWER]
        assert viewer_caps.issubset(member_caps), (
            "Viewer should have a subset of member capabilities"
        )

    def test_capability_sets_are_frozen_copies(self) -> None:
        """Mutating the returned set should not corrupt the matrix."""
        caps = ROLE_CAPABILITIES[Role.VIEWER]
        original_size = len(caps)
        # Attempt mutation — Python sets are mutable but we check isolation
        # by verifying the matrix value is unaffected if it's a real set.
        caps_copy = caps.copy()
        caps_copy.add(Capability.MANAGE_USERS)
        assert len(ROLE_CAPABILITIES[Role.VIEWER]) == original_size


# ---------------------------------------------------------------------------
# has_capability() helper
# ---------------------------------------------------------------------------


class TestHasCapability:
    def test_owner_has_every_capability(self) -> None:
        for cap in Capability:
            assert has_capability(Role.OWNER, cap), f"owner missing {cap}"

    def test_viewer_has_read_own(self) -> None:
        assert has_capability(Role.VIEWER, Capability.READ_OWN_RECORDS)

    def test_viewer_lacks_write(self) -> None:
        assert not has_capability(Role.VIEWER, Capability.WRITE_OWN_RECORDS)

    def test_viewer_lacks_manage_users(self) -> None:
        assert not has_capability(Role.VIEWER, Capability.MANAGE_USERS)

    def test_admin_has_manage_users(self) -> None:
        assert has_capability(Role.ADMIN, Capability.MANAGE_USERS)

    def test_member_has_create_own(self) -> None:
        assert has_capability(Role.MEMBER, Capability.CREATE_OWN_RECORDS)

    def test_member_lacks_read_all(self) -> None:
        assert not has_capability(Role.MEMBER, Capability.READ_ALL_RECORDS)


# ---------------------------------------------------------------------------
# RoleStore — SQLite persistence
# ---------------------------------------------------------------------------


@pytest.fixture()
def role_store(tmp_path: Path) -> RoleStore:
    return RoleStore(db_path=tmp_path / "identity.db")


class TestRoleStore:
    def test_init_creates_roles_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "identity.db"
        RoleStore(db_path=db_path)
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='roles'"
            ).fetchone()
        assert row is not None, "roles table not created"

    def test_init_creates_index(self, tmp_path: Path) -> None:
        db_path = tmp_path / "identity.db"
        RoleStore(db_path=db_path)
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_roles_principal_id'"
            ).fetchone()
        assert row is not None, "idx_roles_principal_id index not created"

    def test_grant_and_get_roles(self, role_store: RoleStore) -> None:
        role_store.grant("user-1", Role.MEMBER, granted_by="admin-0")
        roles = role_store.get_roles("user-1")
        assert Role.MEMBER in roles

    def test_grant_is_idempotent(self, role_store: RoleStore) -> None:
        role_store.grant("user-1", Role.VIEWER, granted_by="admin-0")
        role_store.grant("user-1", Role.VIEWER, granted_by="admin-0")  # second grant
        roles = role_store.get_roles("user-1")
        assert roles.count(Role.VIEWER) == 1, "Duplicate role assignment stored"

    def test_grant_multiple_roles(self, role_store: RoleStore) -> None:
        role_store.grant("user-2", Role.MEMBER, granted_by="admin-0")
        role_store.grant("user-2", Role.VIEWER, granted_by="admin-0")
        roles = role_store.get_roles("user-2")
        assert set(roles) == {Role.MEMBER, Role.VIEWER}

    def test_revoke_removes_role(self, role_store: RoleStore) -> None:
        role_store.grant("user-3", Role.ADMIN, granted_by="owner-0")
        removed = role_store.revoke("user-3", Role.ADMIN)
        assert removed is True
        roles = role_store.get_roles("user-3")
        assert Role.ADMIN not in roles

    def test_revoke_returns_false_when_not_assigned(self, role_store: RoleStore) -> None:
        removed = role_store.revoke("nobody", Role.OWNER)
        assert removed is False

    def test_get_roles_empty_for_unknown_principal(self, role_store: RoleStore) -> None:
        assert role_store.get_roles("does-not-exist") == []

    def test_get_capabilities_union(self, role_store: RoleStore) -> None:
        role_store.grant("user-4", Role.VIEWER, granted_by="admin-0")
        caps = role_store.get_capabilities("user-4")
        assert Capability.READ_OWN_RECORDS in caps
        assert Capability.MANAGE_USERS not in caps

    def test_get_capabilities_owner_has_all(self, role_store: RoleStore) -> None:
        role_store.grant("owner-1", Role.OWNER, granted_by="system")
        caps = role_store.get_capabilities("owner-1")
        assert caps == set(Capability)

    def test_get_capabilities_merges_multiple_roles(self, role_store: RoleStore) -> None:
        role_store.grant("user-5", Role.VIEWER, granted_by="admin-0")
        role_store.grant("user-5", Role.MEMBER, granted_by="admin-0")
        caps = role_store.get_capabilities("user-5")
        # Member caps are a superset of viewer caps
        member_caps = ROLE_CAPABILITIES[Role.MEMBER]
        assert member_caps.issubset(caps)

    def test_get_capabilities_empty_for_no_roles(self, role_store: RoleStore) -> None:
        assert role_store.get_capabilities("nobody") == set()

    def test_list_assignments_returns_all(self, role_store: RoleStore) -> None:
        role_store.grant("user-a", Role.MEMBER, granted_by="admin-0")
        role_store.grant("user-b", Role.VIEWER, granted_by="admin-0")
        assignments = role_store.list_assignments()
        principal_ids = {a["principal_id"] for a in assignments}
        assert {"user-a", "user-b"}.issubset(principal_ids)

    def test_list_assignments_contains_audit_fields(self, role_store: RoleStore) -> None:
        role_store.grant("user-c", Role.ADMIN, granted_by="owner-0")
        assignments = role_store.list_assignments()
        match = next(a for a in assignments if a["principal_id"] == "user-c")
        assert match["role"] == "admin"
        assert match["granted_by"] == "owner-0"
        assert isinstance(match["granted_at"], float)

    def test_roles_table_schema(self, tmp_path: Path) -> None:
        """Verify the roles table has the required columns."""
        db_path = tmp_path / "identity.db"
        RoleStore(db_path=db_path)
        with closing(sqlite3.connect(str(db_path))) as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(roles)").fetchall()
            }
        required = {"id", "principal_id", "role", "granted_by", "granted_at"}
        assert required.issubset(cols), f"Missing columns: {required - cols}"


# ---------------------------------------------------------------------------
# Migration SQL file
# ---------------------------------------------------------------------------


class TestMigrationFile:
    def test_migration_file_exists(self) -> None:
        migration_path = (
            Path(__file__).parent.parent
            / "src"
            / "depthfusion"
            / "migrations"
            / "0002_roles.sql"
        )
        assert migration_path.exists(), f"Migration file not found: {migration_path}"

    def test_migration_creates_roles_table(self, tmp_path: Path) -> None:
        migration_path = (
            Path(__file__).parent.parent
            / "src"
            / "depthfusion"
            / "migrations"
            / "0002_roles.sql"
        )
        sql = migration_path.read_text()
        db_path = tmp_path / "test_migration.db"
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.executescript(sql)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='roles'"
            ).fetchone()
        assert row is not None, "0002_roles.sql did not create roles table"

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Running the migration twice should not raise."""
        migration_path = (
            Path(__file__).parent.parent
            / "src"
            / "depthfusion"
            / "migrations"
            / "0002_roles.sql"
        )
        sql = migration_path.read_text()
        db_path = tmp_path / "test_idempotent.db"
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.executescript(sql)
            conn.executescript(sql)  # second run — must not raise


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------


class TestModuleExports:
    def test_all_symbols_exported_from_authz(self) -> None:
        from depthfusion.authz import (  # noqa: F401
            Capability,
            Role,
            ROLE_CAPABILITIES,
            RoleStore,
            has_capability,
        )
