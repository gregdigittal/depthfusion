"""Tests for T-559: Capability matrix doc + tests per role.

Parameterized tests assert that each role has **exactly** the expected
capabilities — no more, no less.  Any divergence between the code and the
capability-matrix.md document will cause a test failure here.

Expected capability sets are defined once in EXPECTED_CAPS below so that
maintainers have a single place to update when the matrix changes.
"""
from __future__ import annotations

from typing import Set

import pytest

from depthfusion.authz.roles import Capability, Role, ROLE_CAPABILITIES


# ---------------------------------------------------------------------------
# Expected capability sets (canonical definition for test assertions)
# ---------------------------------------------------------------------------
#
# These must match docs/capability-matrix.md exactly.
# When you add a new capability, update BOTH this dict AND the docs.
#
EXPECTED_CAPS: dict[Role, set[Capability]] = {
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
    Role.OWNER: set(Capability),  # All capabilities
}


# ---------------------------------------------------------------------------
# Parameterized exact-match tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
class TestRoleCapabilitiesExact:
    """Each role must have EXACTLY the expected capabilities — no extras, no gaps."""

    def test_has_expected_capabilities(self, role: Role) -> None:
        actual = ROLE_CAPABILITIES[role]
        expected = EXPECTED_CAPS[role]
        missing = expected - actual
        assert not missing, (
            f"Role '{role.value}' is MISSING capabilities: "
            + ", ".join(c.value for c in sorted(missing, key=lambda c: c.value))
        )

    def test_has_no_extra_capabilities(self, role: Role) -> None:
        actual = ROLE_CAPABILITIES[role]
        expected = EXPECTED_CAPS[role]
        extra = actual - expected
        assert not extra, (
            f"Role '{role.value}' has UNEXPECTED capabilities: "
            + ", ".join(c.value for c in sorted(extra, key=lambda c: c.value))
        )


# ---------------------------------------------------------------------------
# Hierarchy (subset) tests
# ---------------------------------------------------------------------------


class TestRoleHierarchy:
    """Viewer ⊂ member ⊂ admin ⊂ owner — strict subset inclusion."""

    def test_owner_is_superset_of_admin(self) -> None:
        assert EXPECTED_CAPS[Role.ADMIN].issubset(EXPECTED_CAPS[Role.OWNER])

    def test_admin_is_superset_of_member(self) -> None:
        assert EXPECTED_CAPS[Role.MEMBER].issubset(EXPECTED_CAPS[Role.ADMIN])

    def test_member_is_superset_of_viewer(self) -> None:
        assert EXPECTED_CAPS[Role.VIEWER].issubset(EXPECTED_CAPS[Role.MEMBER])

    def test_owner_has_all_capabilities(self) -> None:
        assert EXPECTED_CAPS[Role.OWNER] == set(Capability)

    def test_viewer_is_minimal(self) -> None:
        """Viewer must have the minimum possible capability set (read-only shared)."""
        viewer_caps = EXPECTED_CAPS[Role.VIEWER]
        # Viewer should only have read capabilities — no writes, no admin
        non_read_caps = set(Capability) - {
            Capability.READ_OWN_RECORDS,
            Capability.READ_SHARED_RECORDS,
        }
        overlap = viewer_caps & non_read_caps
        assert not overlap, f"Viewer has unexpected non-read capabilities: {overlap}"


# ---------------------------------------------------------------------------
# Boundary: capabilities exclusive to specific roles
# ---------------------------------------------------------------------------


class TestCapabilityExclusivity:
    """Certain capabilities must belong to exactly one role tier."""

    def test_assign_roles_owner_only(self) -> None:
        """ASSIGN_ROLES must NOT be granted to admin, member, or viewer."""
        for role in (Role.ADMIN, Role.MEMBER, Role.VIEWER):
            assert Capability.ASSIGN_ROLES not in ROLE_CAPABILITIES[role], (
                f"ASSIGN_ROLES must be owner-only, but {role.value} has it"
            )

    def test_revoke_roles_owner_only(self) -> None:
        """REVOKE_ROLES must NOT be granted to admin, member, or viewer."""
        for role in (Role.ADMIN, Role.MEMBER, Role.VIEWER):
            assert Capability.REVOKE_ROLES not in ROLE_CAPABILITIES[role], (
                f"REVOKE_ROLES must be owner-only, but {role.value} has it"
            )

    def test_read_restricted_owner_only(self) -> None:
        """READ_RESTRICTED must NOT be granted to admin, member, or viewer."""
        for role in (Role.ADMIN, Role.MEMBER, Role.VIEWER):
            assert Capability.READ_RESTRICTED not in ROLE_CAPABILITIES[role], (
                f"READ_RESTRICTED must be owner-only, but {role.value} has it"
            )

    def test_write_all_records_owner_only(self) -> None:
        """WRITE_ALL_RECORDS must NOT be granted to admin, member, or viewer."""
        for role in (Role.ADMIN, Role.MEMBER, Role.VIEWER):
            assert Capability.WRITE_ALL_RECORDS not in ROLE_CAPABILITIES[role], (
                f"WRITE_ALL_RECORDS must be owner-only, but {role.value} has it"
            )

    def test_manage_users_admin_and_above(self) -> None:
        """MANAGE_USERS must be granted to admin and owner, not member/viewer."""
        assert Capability.MANAGE_USERS in ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.MANAGE_USERS in ROLE_CAPABILITIES[Role.OWNER]
        assert Capability.MANAGE_USERS not in ROLE_CAPABILITIES[Role.MEMBER]
        assert Capability.MANAGE_USERS not in ROLE_CAPABILITIES[Role.VIEWER]

    def test_view_audit_log_admin_and_above(self) -> None:
        """VIEW_AUDIT_LOG must be granted to admin and owner, not member/viewer."""
        assert Capability.VIEW_AUDIT_LOG in ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.VIEW_AUDIT_LOG in ROLE_CAPABILITIES[Role.OWNER]
        assert Capability.VIEW_AUDIT_LOG not in ROLE_CAPABILITIES[Role.MEMBER]
        assert Capability.VIEW_AUDIT_LOG not in ROLE_CAPABILITIES[Role.VIEWER]

    def test_read_all_records_admin_and_above(self) -> None:
        """READ_ALL_RECORDS must be granted to admin and owner, not member/viewer."""
        assert Capability.READ_ALL_RECORDS in ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.READ_ALL_RECORDS in ROLE_CAPABILITIES[Role.OWNER]
        assert Capability.READ_ALL_RECORDS not in ROLE_CAPABILITIES[Role.MEMBER]
        assert Capability.READ_ALL_RECORDS not in ROLE_CAPABILITIES[Role.VIEWER]

    def test_create_own_records_member_and_above(self) -> None:
        """CREATE_OWN_RECORDS must be granted to member and above, not viewer."""
        assert Capability.CREATE_OWN_RECORDS in ROLE_CAPABILITIES[Role.MEMBER]
        assert Capability.CREATE_OWN_RECORDS in ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.CREATE_OWN_RECORDS in ROLE_CAPABILITIES[Role.OWNER]
        assert Capability.CREATE_OWN_RECORDS not in ROLE_CAPABILITIES[Role.VIEWER]

    def test_write_own_records_member_and_above(self) -> None:
        """WRITE_OWN_RECORDS must be granted to member and above, not viewer."""
        assert Capability.WRITE_OWN_RECORDS in ROLE_CAPABILITIES[Role.MEMBER]
        assert Capability.WRITE_OWN_RECORDS in ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.WRITE_OWN_RECORDS in ROLE_CAPABILITIES[Role.OWNER]
        assert Capability.WRITE_OWN_RECORDS not in ROLE_CAPABILITIES[Role.VIEWER]


# ---------------------------------------------------------------------------
# Matrix completeness: all capabilities are covered by at least one role
# ---------------------------------------------------------------------------


class TestMatrixCompleteness:
    def test_every_capability_covered_by_owner(self) -> None:
        """Owner must grant every defined capability — no orphan capabilities."""
        owner_caps = ROLE_CAPABILITIES[Role.OWNER]
        all_caps = set(Capability)
        orphans = all_caps - owner_caps
        assert not orphans, (
            f"The following capabilities are not granted by any role: "
            + ", ".join(c.value for c in sorted(orphans, key=lambda c: c.value))
        )

    def test_expected_caps_covers_all_roles(self) -> None:
        """EXPECTED_CAPS must define an entry for every Role."""
        for role in Role:
            assert role in EXPECTED_CAPS, (
                f"Role {role.value!r} missing from EXPECTED_CAPS in test_role_matrix.py"
            )

    def test_capability_count(self) -> None:
        """Sanity check: there are exactly 13 defined capabilities."""
        # Update this if capabilities are intentionally added/removed.
        assert len(set(Capability)) == 13, (
            f"Expected 13 capabilities, found {len(set(Capability))}. "
            "Update EXPECTED_CAPS and docs/capability-matrix.md."
        )


# ---------------------------------------------------------------------------
# Role count
# ---------------------------------------------------------------------------


class TestRoleCount:
    def test_four_roles_defined(self) -> None:
        """There must be exactly 4 canonical roles."""
        assert len(set(Role)) == 4

    def test_role_values(self) -> None:
        """Role values must be the canonical four."""
        assert {r.value for r in Role} == {"owner", "admin", "member", "viewer"}
