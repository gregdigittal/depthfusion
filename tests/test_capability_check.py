"""Tests for T-557: Capability-check service.

Covers:
- require_capability: role × capability × acl matrix
- AuthorizationError attributes (principal_id, capability, reason)
- _capabilities_for_principal: multiple roles, unknown groups, empty groups
- Principal with no roles → denied
- Principal in ACL but missing capability → denied
- Principal has capability but not in ACL → denied
- Principal has capability AND in ACL → allowed
- Owner role → all capabilities pass
- Viewer role → restricted capabilities denied
"""
from __future__ import annotations

import pytest

from depthfusion.authz.capability_check import AuthorizationError, require_capability
from depthfusion.authz.roles import ROLE_CAPABILITIES, Capability, Role
from depthfusion.identity.models import Principal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_principal(
    principal_id: str = "user-1",
    groups: list[str] | None = None,
) -> Principal:
    return Principal(
        principal_id=principal_id,
        upn=f"{principal_id}@example.com",
        display_name=principal_id,
        groups=groups or [],
    )


# ---------------------------------------------------------------------------
# AuthorizationError
# ---------------------------------------------------------------------------


class TestAuthorizationError:
    def test_str_is_reason(self) -> None:
        err = AuthorizationError("pid", "some reason", Capability.READ_OWN_RECORDS)
        assert "some reason" in str(err)

    def test_attributes(self) -> None:
        err = AuthorizationError("pid-42", "denied", Capability.WRITE_ALL_RECORDS)
        assert err.principal_id == "pid-42"
        assert err.capability == Capability.WRITE_ALL_RECORDS
        assert err.reason == "denied"

    def test_capability_optional(self) -> None:
        err = AuthorizationError("pid", "denied")
        assert err.capability is None

    def test_repr(self) -> None:
        err = AuthorizationError("pid", "denied", Capability.MANAGE_USERS)
        r = repr(err)
        assert "pid" in r
        assert "manage_users" in r


# ---------------------------------------------------------------------------
# require_capability — capability check failures
# ---------------------------------------------------------------------------


class TestRequireCapabilityCapabilityDenied:
    """Principal has correct ACL entry but the role does not grant the capability."""

    def test_viewer_cannot_create(self) -> None:
        principal = make_principal("alice", groups=["viewer"])
        with pytest.raises(AuthorizationError) as exc_info:
            require_capability(
                principal,
                Capability.CREATE_OWN_RECORDS,
                resource_acl=["alice"],
            )
        err = exc_info.value
        assert err.principal_id == "alice"
        assert err.capability == Capability.CREATE_OWN_RECORDS

    def test_viewer_cannot_write(self) -> None:
        principal = make_principal("bob", groups=["viewer"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.WRITE_OWN_RECORDS,
                resource_acl=["bob"],
            )

    def test_viewer_cannot_manage_users(self) -> None:
        principal = make_principal("carol", groups=["viewer"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.MANAGE_USERS,
                resource_acl=["carol"],
            )

    def test_member_cannot_read_all_records(self) -> None:
        principal = make_principal("dave", groups=["member"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.READ_ALL_RECORDS,
                resource_acl=["dave"],
            )

    def test_member_cannot_manage_users(self) -> None:
        principal = make_principal("eve", groups=["member"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.MANAGE_USERS,
                resource_acl=["eve"],
            )

    def test_admin_cannot_assign_roles(self) -> None:
        """ASSIGN_ROLES is an owner-only capability."""
        principal = make_principal("frank", groups=["admin"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.ASSIGN_ROLES,
                resource_acl=["frank"],
            )

    def test_no_role_groups_denied(self) -> None:
        """Principal with no recognised role groups is denied every capability."""
        principal = make_principal("ghost", groups=[])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.READ_OWN_RECORDS,
                resource_acl=["ghost"],
            )

    def test_unknown_group_ignored(self) -> None:
        """Unrecognised group names are silently skipped."""
        principal = make_principal("unk", groups=["superuser", "dev-team"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.READ_OWN_RECORDS,
                resource_acl=["unk"],
            )


# ---------------------------------------------------------------------------
# require_capability — ACL check failures
# ---------------------------------------------------------------------------


class TestRequireCapabilityAclDenied:
    """Principal has the capability but is not in the resource ACL."""

    def test_owner_not_in_acl(self) -> None:
        principal = make_principal("owner-user", groups=["owner"])
        with pytest.raises(AuthorizationError) as exc_info:
            require_capability(
                principal,
                Capability.READ_ALL_RECORDS,
                resource_acl=["other-user"],
            )
        assert exc_info.value.principal_id == "owner-user"

    def test_admin_not_in_acl(self) -> None:
        principal = make_principal("admin-user", groups=["admin"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.READ_OWN_RECORDS,
                resource_acl=[],
            )

    def test_member_not_in_acl(self) -> None:
        principal = make_principal("member-user", groups=["member"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.READ_OWN_RECORDS,
                resource_acl=["someone-else"],
            )

    def test_viewer_not_in_acl(self) -> None:
        principal = make_principal("viewer-user", groups=["viewer"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.READ_SHARED_RECORDS,
                resource_acl=[],
            )

    def test_empty_acl_always_denied(self) -> None:
        """Even an owner is denied when the ACL list is empty."""
        principal = make_principal("super-owner", groups=["owner"])
        with pytest.raises(AuthorizationError):
            require_capability(
                principal,
                Capability.WRITE_ALL_RECORDS,
                resource_acl=[],
            )


# ---------------------------------------------------------------------------
# require_capability — allowed (returns None)
# ---------------------------------------------------------------------------


class TestRequireCapabilityAllowed:
    """Both capability AND ACL checks pass — function returns None."""

    def test_owner_all_capabilities(self) -> None:
        """Owner should pass every capability when in the ACL."""
        principal = make_principal("owner-1", groups=["owner"])
        for cap in Capability:
            require_capability(principal, cap, resource_acl=["owner-1"])

    def test_admin_read_all(self) -> None:
        principal = make_principal("admin-1", groups=["admin"])
        require_capability(
            principal,
            Capability.READ_ALL_RECORDS,
            resource_acl=["admin-1"],
        )

    def test_admin_manage_users(self) -> None:
        principal = make_principal("admin-2", groups=["admin"])
        require_capability(
            principal,
            Capability.MANAGE_USERS,
            resource_acl=["admin-2"],
        )

    def test_member_create_own(self) -> None:
        principal = make_principal("member-1", groups=["member"])
        require_capability(
            principal,
            Capability.CREATE_OWN_RECORDS,
            resource_acl=["member-1"],
        )

    def test_member_write_own(self) -> None:
        principal = make_principal("member-2", groups=["member"])
        require_capability(
            principal,
            Capability.WRITE_OWN_RECORDS,
            resource_acl=["member-2"],
        )

    def test_member_read_own(self) -> None:
        principal = make_principal("member-3", groups=["member"])
        require_capability(
            principal,
            Capability.READ_OWN_RECORDS,
            resource_acl=["member-3"],
        )

    def test_member_read_shared(self) -> None:
        principal = make_principal("member-4", groups=["member"])
        require_capability(
            principal,
            Capability.READ_SHARED_RECORDS,
            resource_acl=["member-4"],
        )

    def test_viewer_read_own(self) -> None:
        principal = make_principal("viewer-1", groups=["viewer"])
        require_capability(
            principal,
            Capability.READ_OWN_RECORDS,
            resource_acl=["viewer-1"],
        )

    def test_viewer_read_shared(self) -> None:
        principal = make_principal("viewer-2", groups=["viewer"])
        require_capability(
            principal,
            Capability.READ_SHARED_RECORDS,
            resource_acl=["viewer-2"],
        )

    def test_multiple_roles_union(self) -> None:
        """A principal with both viewer+member gets the union of capabilities."""
        principal = make_principal("dual", groups=["viewer", "member"])
        # member grants write_own_records; viewer alone would not
        require_capability(
            principal,
            Capability.WRITE_OWN_RECORDS,
            resource_acl=["dual"],
        )

    def test_acl_with_multiple_entries(self) -> None:
        principal = make_principal("user-x", groups=["member"])
        require_capability(
            principal,
            Capability.READ_OWN_RECORDS,
            resource_acl=["user-a", "user-x", "user-b"],
        )

    def test_returns_none(self) -> None:
        principal = make_principal("owner-ret", groups=["owner"])
        result = require_capability(
            principal,
            Capability.MANAGE_SETTINGS,
            resource_acl=["owner-ret"],
        )
        assert result is None


# ---------------------------------------------------------------------------
# Full role × capability matrix
# ---------------------------------------------------------------------------


class TestRoleCapabilityMatrix:
    """Exhaustive matrix: every role × every capability × in/out of ACL."""

    @pytest.mark.parametrize("cap", list(Capability))
    def test_owner_passes_all_caps_in_acl(self, cap: Capability) -> None:
        principal = make_principal("o", groups=["owner"])
        require_capability(principal, cap, resource_acl=["o"])

    @pytest.mark.parametrize(
        "cap",
        list(ROLE_CAPABILITIES[Role.ADMIN]),
    )
    def test_admin_passes_own_caps_in_acl(self, cap: Capability) -> None:
        principal = make_principal("a", groups=["admin"])
        require_capability(principal, cap, resource_acl=["a"])

    @pytest.mark.parametrize(
        "cap",
        list(set(Capability) - ROLE_CAPABILITIES[Role.ADMIN]),
    )
    def test_admin_fails_caps_not_in_role(self, cap: Capability) -> None:
        principal = make_principal("a", groups=["admin"])
        with pytest.raises(AuthorizationError):
            require_capability(principal, cap, resource_acl=["a"])

    @pytest.mark.parametrize(
        "cap",
        list(ROLE_CAPABILITIES[Role.MEMBER]),
    )
    def test_member_passes_own_caps_in_acl(self, cap: Capability) -> None:
        principal = make_principal("m", groups=["member"])
        require_capability(principal, cap, resource_acl=["m"])

    @pytest.mark.parametrize(
        "cap",
        list(set(Capability) - ROLE_CAPABILITIES[Role.MEMBER]),
    )
    def test_member_fails_caps_not_in_role(self, cap: Capability) -> None:
        principal = make_principal("m", groups=["member"])
        with pytest.raises(AuthorizationError):
            require_capability(principal, cap, resource_acl=["m"])

    @pytest.mark.parametrize(
        "cap",
        list(ROLE_CAPABILITIES[Role.VIEWER]),
    )
    def test_viewer_passes_own_caps_in_acl(self, cap: Capability) -> None:
        principal = make_principal("v", groups=["viewer"])
        require_capability(principal, cap, resource_acl=["v"])

    @pytest.mark.parametrize(
        "cap",
        list(set(Capability) - ROLE_CAPABILITIES[Role.VIEWER]),
    )
    def test_viewer_fails_caps_not_in_role(self, cap: Capability) -> None:
        principal = make_principal("v", groups=["viewer"])
        with pytest.raises(AuthorizationError):
            require_capability(principal, cap, resource_acl=["v"])

    @pytest.mark.parametrize("cap", list(Capability))
    def test_any_role_fails_if_not_in_acl(self, cap: Capability) -> None:
        """Every role fails every cap when the principal is not in the ACL."""
        principal = make_principal("outsider", groups=["owner"])
        with pytest.raises(AuthorizationError):
            require_capability(principal, cap, resource_acl=["someone-else"])
