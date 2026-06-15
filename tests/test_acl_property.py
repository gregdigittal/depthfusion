"""T-570: Property-based tests — no path returns ALLOW when ACL excludes principal.

Security invariant
------------------
The PolicyEngine must never grant access to a principal that is not in the
resource ACL, UNLESS the principal holds an explicit admin-override capability:

* ``READ_ALL_RECORDS`` bypasses the ACL gate for read-class capabilities.
* ``WRITE_ALL_RECORDS`` bypasses the ACL gate for write-class capabilities.

Any other combination — including roles that hold powerful write capabilities
but lack ``WRITE_ALL_RECORDS`` (e.g. ADMIN for write-class caps) — must still
be denied when the principal is absent from ``acl_allow``.

Test strategy
-------------
* Exhaustive parametrised matrix over (Role, Capability) pairs for the
  common cases (deterministic, fast).
* Spot checks for ACL-membership edge cases: empty list, ``None``, near-match
  IDs, multi-entry lists that don't include the principal.
* Negative confirmation: when the principal IS in the ACL, checks that the
  RBAC pass/fail still governs correctly (ACL alone is not sufficient).
"""
from __future__ import annotations

import pytest

from depthfusion.authz.policy_engine import _READ_CAPS, PolicyEngine
from depthfusion.authz.roles import ROLE_CAPABILITIES, Capability, Role
from depthfusion.identity.models import Principal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _p(pid: str, groups: list[str]) -> Principal:
    return Principal(principal_id=pid, groups=groups)


def _role_principal(role: Role, pid: str = "test-pid") -> Principal:
    return _p(pid, [role.value])


# ---------------------------------------------------------------------------
# Capability classification helpers (mirrors policy_engine internals)
# ---------------------------------------------------------------------------

_WRITE_CAP_SET = frozenset(
    {
        Capability.CREATE_OWN_RECORDS,
        Capability.WRITE_OWN_RECORDS,
        Capability.WRITE_ALL_RECORDS,
    }
)


def _is_write_cap(cap: Capability) -> bool:
    return cap in _WRITE_CAP_SET


# ---------------------------------------------------------------------------
# T-570-A: No-override roles — DENY for all capabilities when not in ACL
# ---------------------------------------------------------------------------


class TestNoOverrideRolesACLRequired:
    """MEMBER and VIEWER have no admin-override capabilities.

    For every capability in the enum, decide() must return DENY when the
    principal is not listed in acl_allow.
    """

    @pytest.fixture(autouse=True)
    def _engine(self) -> None:
        self.engine = PolicyEngine(cache_ttl=0.0)  # no caching — each call fresh

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    @pytest.mark.parametrize("cap", list(Capability))
    def test_deny_when_not_in_acl(self, role: Role, cap: Capability) -> None:
        caps = ROLE_CAPABILITIES[role]
        assert Capability.READ_ALL_RECORDS not in caps
        assert Capability.WRITE_ALL_RECORDS not in caps

        p = _role_principal(role)
        dec = self.engine.decide(p, cap, {"acl_allow": ["other-user-1", "other-user-2"]})
        assert not dec.allow, (
            f"role={role.value} cap={cap.value}: expected deny when not in ACL"
        )

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    @pytest.mark.parametrize("cap", list(Capability))
    def test_deny_when_acl_is_empty(self, role: Role, cap: Capability) -> None:
        p = _role_principal(role)
        dec = self.engine.decide(p, cap, {"acl_allow": []})
        assert not dec.allow

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    @pytest.mark.parametrize("cap", list(Capability))
    def test_deny_when_acl_key_absent(self, role: Role, cap: Capability) -> None:
        """Resource dict without acl_allow key is treated as empty ACL."""
        p = _role_principal(role)
        dec = self.engine.decide(p, cap, {})
        assert not dec.allow


# ---------------------------------------------------------------------------
# T-570-B: ADMIN role — write capabilities still require ACL membership
# ---------------------------------------------------------------------------


class TestAdminWriteCapsRequireACL:
    """ADMIN has READ_ALL_RECORDS but NOT WRITE_ALL_RECORDS.

    For write-class capabilities, the admin-override does not fire and ACL
    membership is required.  For read-class capabilities, the override fires
    and access is granted without ACL membership.
    """

    @pytest.fixture(autouse=True)
    def _engine(self) -> None:
        self.engine = PolicyEngine(cache_ttl=0.0)

    @pytest.mark.parametrize("cap", [c for c in Capability if _is_write_cap(c)])
    def test_admin_denied_write_cap_when_not_in_acl(self, cap: Capability) -> None:
        caps = ROLE_CAPABILITIES[Role.ADMIN]
        assert Capability.READ_ALL_RECORDS in caps
        assert Capability.WRITE_ALL_RECORDS not in caps

        p = _role_principal(Role.ADMIN, pid="admin-user")
        dec = self.engine.decide(p, cap, {"acl_allow": ["someone-else"]})
        assert not dec.allow, (
            f"ADMIN should be denied write cap={cap.value} when not in ACL"
        )

    @pytest.mark.parametrize(
        "cap",
        [
            c for c in Capability
            if c in _READ_CAPS and c in ROLE_CAPABILITIES[Role.ADMIN]
        ],
    )
    def test_admin_allowed_read_cap_via_override(self, cap: Capability) -> None:
        """READ_ALL_RECORDS fires for read-class capabilities ADMIN holds — no ACL needed.

        READ_RESTRICTED is excluded: ADMIN does not hold that capability (RBAC
        fails before the ACL check for that capability, so the override never
        fires regardless of ACL).
        """
        p = _role_principal(Role.ADMIN, pid="admin-user")
        dec = self.engine.decide(p, cap, {"acl_allow": []})
        assert dec.allow, (
            f"ADMIN should be allowed read cap={cap.value} via READ_ALL_RECORDS override"
        )


# ---------------------------------------------------------------------------
# T-570-C: ACL membership edge cases
# ---------------------------------------------------------------------------


class TestACLMembershipEdgeCases:
    """Verify that only exact-match principal_id grants ACL admission."""

    @pytest.fixture(autouse=True)
    def _engine(self) -> None:
        self.engine = PolicyEngine(cache_ttl=0.0)

    def _member(self, pid: str) -> Principal:
        return _p(pid, ["member"])

    @pytest.mark.parametrize("cap", [Capability.READ_OWN_RECORDS, Capability.WRITE_OWN_RECORDS])
    def test_prefix_match_does_not_grant_access(self, cap: Capability) -> None:
        """'user-1' should NOT grant access to 'user-10' or 'user-1-extra'."""
        p = self._member("user-1")
        # ACL contains longer IDs that start with the principal's id
        dec = self.engine.decide(p, cap, {"acl_allow": ["user-10", "user-1-extra", "user-1x"]})
        assert not dec.allow, f"Prefix-matched IDs must not grant access for cap={cap.value}"

    @pytest.mark.parametrize("cap", [Capability.READ_OWN_RECORDS, Capability.WRITE_OWN_RECORDS])
    def test_suffix_match_does_not_grant_access(self, cap: Capability) -> None:
        """'user' should NOT grant access to 'xuser' or 'super-user'."""
        p = self._member("user")
        dec = self.engine.decide(p, cap, {"acl_allow": ["xuser", "super-user", "my-user"]})
        assert not dec.allow

    @pytest.mark.parametrize("cap", [Capability.READ_OWN_RECORDS, Capability.WRITE_OWN_RECORDS])
    def test_exact_match_grants_access(self, cap: Capability) -> None:
        p = self._member("exact-user")
        dec = self.engine.decide(p, cap, {"acl_allow": ["other", "exact-user", "another"]})
        assert dec.allow, f"Exact match in acl_allow must grant access for cap={cap.value}"

    def test_empty_principal_id_requires_explicit_match(self) -> None:
        """An empty-string principal must be explicitly in acl_allow to pass."""
        p = _p("", ["member"])
        dec = self.engine.decide(p, Capability.READ_OWN_RECORDS, {"acl_allow": []})
        assert not dec.allow

        dec2 = self.engine.decide(p, Capability.READ_OWN_RECORDS, {"acl_allow": [""]})
        assert dec2.allow, "Empty-string principal explicitly listed must be allowed"

    @pytest.mark.parametrize("cap", [Capability.READ_OWN_RECORDS, Capability.WRITE_OWN_RECORDS])
    def test_large_acl_with_principal_present(self, cap: Capability) -> None:
        """Linear scan through a large ACL must still find the principal."""
        pid = "target-user"
        p = self._member(pid)
        large_acl = [f"user-{i}" for i in range(500)] + [pid] + [f"user-{i}" for i in range(500, 1000)]
        dec = self.engine.decide(p, cap, {"acl_allow": large_acl})
        assert dec.allow, f"Principal present in large ACL must be allowed for cap={cap.value}"

    @pytest.mark.parametrize("cap", [Capability.READ_OWN_RECORDS, Capability.WRITE_OWN_RECORDS])
    def test_large_acl_without_principal(self, cap: Capability) -> None:
        """Large ACL that does not include the principal must deny."""
        pid = "not-here"
        p = self._member(pid)
        large_acl = [f"user-{i}" for i in range(1000)]
        dec = self.engine.decide(p, cap, {"acl_allow": large_acl})
        assert not dec.allow


# ---------------------------------------------------------------------------
# T-570-D: Cross-role, cross-capability deny matrix for zero-overlap cases
# ---------------------------------------------------------------------------


class TestCrossRoleDenyMatrix:
    """All non-override (role, cap) pairs where the role doesn't hold the
    capability at all → deny regardless of ACL membership.
    """

    @pytest.fixture(autouse=True)
    def _engine(self) -> None:
        self.engine = PolicyEngine(cache_ttl=0.0)

    @pytest.mark.parametrize("role", list(Role))
    @pytest.mark.parametrize("cap", list(Capability))
    def test_deny_if_role_lacks_capability_even_in_acl(
        self, role: Role, cap: Capability
    ) -> None:
        """If the role doesn't include the capability, ACL membership can't save it."""
        if cap in ROLE_CAPABILITIES[role]:
            pytest.skip(f"role={role.value} holds cap={cap.value} — skip (not the deny case)")

        p = _role_principal(role, pid=f"{role.value}-tester")
        dec = self.engine.decide(p, cap, {"acl_allow": [p.principal_id]})
        assert not dec.allow, (
            f"role={role.value} does not hold cap={cap.value}; "
            "ACL membership must NOT override missing RBAC grant"
        )


# ---------------------------------------------------------------------------
# T-570-E: Classification + ACL combination — both gates must pass
# ---------------------------------------------------------------------------


class TestClassificationWithACL:
    """Classification check fires after ACL passes; both must allow for access."""

    @pytest.fixture(autouse=True)
    def _engine(self) -> None:
        self.engine = PolicyEngine(cache_ttl=0.0)

    @pytest.mark.parametrize("classification", ["restricted", "confidential"])
    def test_member_in_acl_denied_by_classification(self, classification: str) -> None:
        """Member is in ACL but not allowed for restricted/confidential data."""
        p = _p("member-user", ["member"])
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": classification},
        )
        assert not dec.allow, (
            f"member in ACL but classification={classification} must still deny"
        )

    def test_admin_allowed_restricted_via_acl_override_and_classification(self) -> None:
        """ADMIN: READ_ALL_RECORDS bypasses ACL, 'admin' role is in restricted policy.

        Classification policy for 'restricted': allowed_roles=['admin'].
        ADMIN's group value is 'admin' → passes the classification check.
        Result: ALLOW even with empty acl_allow.
        """
        p = _role_principal(Role.ADMIN, pid="admin-p")
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [], "classification": "restricted"},
        )
        assert dec.allow, (
            "ADMIN bypasses ACL via READ_ALL_RECORDS and 'admin' role is in "
            "restricted.allowed_roles — expected ALLOW"
        )

    def test_owner_denied_restricted_due_to_classification(self) -> None:
        """OWNER's group is 'owner', which is NOT in restricted.allowed_roles=['admin'].

        Even with explicit ACL membership, classification check denies OWNER for
        restricted data.  This documents a deliberate policy choice: 'restricted'
        is scoped to the 'admin' enterprise role, not the 'owner' infrastructure role.
        """
        p = _role_principal(Role.OWNER, pid="owner-p")
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "restricted"},
        )
        assert not dec.allow, (
            "OWNER group='owner' is not in restricted.allowed_roles=['admin'] — "
            "expected DENY despite explicit ACL membership"
        )

    def test_viewer_denied_confidential_by_classification(self) -> None:
        """VIEWER's role is not in confidential.allowed_roles — deny even with ACL."""
        p = _role_principal(Role.VIEWER, pid="viewer-p")
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "confidential"},
        )
        assert not dec.allow
