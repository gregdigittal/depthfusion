"""Tests for service-account issuance + classification-ceiling enforcement (T-624).

Covers:
  - issue_service_account: ceiling default (least-privilege), explicit ceiling,
    read-only scope enforcement, unique token/account_id.
  - filter_records_by_ceiling / is_record_visible: allow (at/below ceiling)
    and deny (above ceiling) cases, plus default-deny on missing/unknown
    classification.
"""
from __future__ import annotations

import pytest

from depthfusion.authz.classification import (
    ClassificationLevel,
    is_within_ceiling,
    level_rank,
)
from depthfusion.identity.service_account import (
    DEFAULT_CEILING,
    ServiceAccount,
    filter_records_by_ceiling,
    is_record_visible,
    issue_service_account,
)

# ---------------------------------------------------------------------------
# Classification ordering helpers
# ---------------------------------------------------------------------------

class TestClassificationOrdering:
    def test_level_rank_is_monotonic(self) -> None:
        assert (
            level_rank(ClassificationLevel.PUBLIC)
            < level_rank(ClassificationLevel.INTERNAL)
            < level_rank(ClassificationLevel.CONFIDENTIAL)
            < level_rank(ClassificationLevel.RESTRICTED)
        )

    def test_ceiling_is_inclusive(self) -> None:
        # confidential ceiling admits public/internal/confidential, not restricted
        c = ClassificationLevel.CONFIDENTIAL
        assert is_within_ceiling(ClassificationLevel.PUBLIC, c)
        assert is_within_ceiling(ClassificationLevel.INTERNAL, c)
        assert is_within_ceiling(ClassificationLevel.CONFIDENTIAL, c)
        assert not is_within_ceiling(ClassificationLevel.RESTRICTED, c)


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------

class TestIssuance:
    def test_default_ceiling_is_least_privilege(self) -> None:
        """Omitting a ceiling yields the least-privilege default (public)."""
        acct = issue_service_account(name="metabase")
        assert acct.ceiling is DEFAULT_CEILING
        assert acct.ceiling is ClassificationLevel.PUBLIC

    def test_explicit_ceiling_is_honored(self) -> None:
        acct = issue_service_account(
            name="grafana", ceiling=ClassificationLevel.CONFIDENTIAL
        )
        assert acct.ceiling is ClassificationLevel.CONFIDENTIAL

    def test_ceiling_accepts_string(self) -> None:
        acct = issue_service_account(name="powerbi", ceiling="internal")
        assert acct.ceiling is ClassificationLevel.INTERNAL

    def test_unknown_ceiling_rejected(self) -> None:
        with pytest.raises(ValueError):
            issue_service_account(name="bad", ceiling="top-secret")

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            issue_service_account(name="   ")

    def test_write_scope_rejected(self) -> None:
        """Service accounts are read-only — a write scope is refused."""
        with pytest.raises(ValueError):
            issue_service_account(name="rw", scopes=("query:read", "records:write"))

    def test_token_and_id_are_unique(self) -> None:
        a = issue_service_account(name="a")
        b = issue_service_account(name="b")
        assert a.token != b.token
        assert a.account_id != b.account_id
        assert a.account_id.startswith("svc-")

    def test_record_is_frozen(self) -> None:
        acct = issue_service_account(name="frozen")
        with pytest.raises(Exception):
            acct.ceiling = ClassificationLevel.RESTRICTED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Ceiling enforcement — allow and deny
# ---------------------------------------------------------------------------

def _acct(ceiling: ClassificationLevel) -> ServiceAccount:
    return issue_service_account(name="t", ceiling=ceiling)


class TestCeilingEnforcement:
    def test_allow_record_at_ceiling(self) -> None:
        acct = _acct(ClassificationLevel.CONFIDENTIAL)
        rec = {"id": 1, "classification": "confidential"}
        assert is_record_visible(rec, acct) is True

    def test_allow_record_below_ceiling(self) -> None:
        acct = _acct(ClassificationLevel.CONFIDENTIAL)
        rec = {"id": 1, "classification": "public"}
        assert is_record_visible(rec, acct) is True

    def test_deny_record_above_ceiling(self) -> None:
        acct = _acct(ClassificationLevel.INTERNAL)
        rec = {"id": 1, "classification": "restricted"}
        assert is_record_visible(rec, acct) is False

    def test_filter_excludes_records_above_ceiling(self) -> None:
        """filter_records_by_ceiling drops only the above-ceiling records."""
        acct = _acct(ClassificationLevel.INTERNAL)
        records = [
            {"id": 1, "classification": "public"},
            {"id": 2, "classification": "internal"},
            {"id": 3, "classification": "confidential"},  # excluded
            {"id": 4, "classification": "restricted"},     # excluded
        ]
        visible = filter_records_by_ceiling(records, acct)
        assert [r["id"] for r in visible] == [1, 2]

    def test_public_ceiling_only_sees_public(self) -> None:
        acct = _acct(ClassificationLevel.PUBLIC)
        records = [
            {"id": 1, "classification": "public"},
            {"id": 2, "classification": "internal"},
        ]
        assert [r["id"] for r in filter_records_by_ceiling(records, acct)] == [1]

    def test_restricted_ceiling_sees_all(self) -> None:
        acct = _acct(ClassificationLevel.RESTRICTED)
        records = [
            {"id": 1, "classification": "public"},
            {"id": 2, "classification": "internal"},
            {"id": 3, "classification": "confidential"},
            {"id": 4, "classification": "restricted"},
        ]
        assert len(filter_records_by_ceiling(records, acct)) == 4

    def test_missing_classification_is_default_deny(self) -> None:
        """A record with no classification is treated as restricted (excluded)."""
        acct = _acct(ClassificationLevel.CONFIDENTIAL)
        rec = {"id": 1}  # no classification key
        assert is_record_visible(rec, acct) is False

    def test_unknown_classification_is_default_deny(self) -> None:
        acct = _acct(ClassificationLevel.CONFIDENTIAL)
        rec = {"id": 1, "classification": "weird"}
        assert is_record_visible(rec, acct) is False

    def test_object_attribute_records_supported(self) -> None:
        """Records exposing .classification as an attribute also work."""
        class Rec:
            classification = "public"

        acct = _acct(ClassificationLevel.PUBLIC)
        assert is_record_visible(Rec(), acct) is True

    def test_ceiling_read_from_account_not_hardcoded(self) -> None:
        """Enforcement reads the ceiling off the issued record (server-returned)."""
        low = _acct(ClassificationLevel.PUBLIC)
        high = _acct(ClassificationLevel.RESTRICTED)
        rec = {"id": 1, "classification": "restricted"}
        # Same record, different accounts → different visibility, driven solely
        # by the account's server-assigned ceiling.
        assert is_record_visible(rec, low) is False
        assert is_record_visible(rec, high) is True
