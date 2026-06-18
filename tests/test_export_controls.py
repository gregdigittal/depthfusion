"""Tests for E-59: Export Controls & IP Protection.

Verifies:
- Restricted/confidential records cannot be exported without approval
- Public/internal records follow their format allowlists
- Watermark policy is correctly propagated
- Custom policy matrix works correctly
"""
from __future__ import annotations

import pytest

from depthfusion.authz.export_controls import (
    ClassificationLevel,
    ExportDecision,
    ExportFormat,
    ExportPolicy,
    ExportPolicyMatrix,
    check_export_allowed,
)

# ---------------------------------------------------------------------------
# Classification: RESTRICTED
# ---------------------------------------------------------------------------

class TestRestrictedExports:
    """Restricted records require approval for ANY export."""

    def test_restricted_denied_without_approval(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
        )
        assert decision.allowed is False
        assert decision.approval_required is True

    def test_restricted_denied_all_formats_without_approval(self) -> None:
        for fmt in ExportFormat:
            decision = check_export_allowed(ClassificationLevel.RESTRICTED, fmt)
            assert decision.allowed is False, f"format {fmt} should be denied without approval"

    def test_restricted_allowed_with_approval_token(self) -> None:
        """An approval token unlocks restricted exports (token validation is T-662)."""
        # The default matrix has allowed_export_formats=[] for RESTRICTED,
        # so even with a token, no format is allowed until T-662 extends the policy.
        # The enforcement here tests the approval_required gate only.
        matrix = ExportPolicyMatrix()
        # Temporarily allow JSON for restricted to test approval path
        matrix.policies[ClassificationLevel.RESTRICTED] = ExportPolicy(
            allowed_export_formats=[ExportFormat.JSON],
            watermark_required=True,
            approval_required=True,
        )
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
            matrix=matrix,
            approval_token="signed-approval-abc123",
        )
        assert decision.allowed is True
        assert decision.watermark_required is True

    def test_restricted_denied_empty_approval_token(self) -> None:
        """Empty string is not a valid approval token."""
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
            approval_token="",
        )
        assert decision.allowed is False
        assert decision.approval_required is True

    def test_restricted_denied_reason_mentions_approval(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.CSV,
        )
        assert "approval" in decision.reason.lower()

    def test_restricted_watermark_required(self) -> None:
        """Watermark flag must be set even on denied restricted exports."""
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
        )
        assert decision.watermark_required is True


# ---------------------------------------------------------------------------
# Classification: CONFIDENTIAL
# ---------------------------------------------------------------------------

class TestConfidentialExports:
    """Confidential records: limited formats, watermark required, no approval needed."""

    def test_confidential_json_allowed(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.JSON,
        )
        assert decision.allowed is True
        assert decision.watermark_required is True

    def test_confidential_csv_allowed(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.CSV,
        )
        assert decision.allowed is True

    def test_confidential_pdf_denied(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.PDF,
        )
        assert decision.allowed is False
        assert "confidential" in decision.reason.lower()

    def test_confidential_raw_denied(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.RAW,
        )
        assert decision.allowed is False

    def test_confidential_markdown_denied(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.MARKDOWN,
        )
        assert decision.allowed is False

    def test_confidential_no_approval_required(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.JSON,
        )
        assert decision.approval_required is False


# ---------------------------------------------------------------------------
# Classification: INTERNAL
# ---------------------------------------------------------------------------

class TestInternalExports:
    """Internal records: broader format access, no watermark, no approval."""

    @pytest.mark.parametrize("fmt", [
        ExportFormat.JSON,
        ExportFormat.CSV,
        ExportFormat.MARKDOWN,
    ])
    def test_internal_allowed_formats(self, fmt: ExportFormat) -> None:
        decision = check_export_allowed(ClassificationLevel.INTERNAL, fmt)
        assert decision.allowed is True
        assert decision.watermark_required is False

    @pytest.mark.parametrize("fmt", [ExportFormat.PDF, ExportFormat.RAW])
    def test_internal_denied_formats(self, fmt: ExportFormat) -> None:
        decision = check_export_allowed(ClassificationLevel.INTERNAL, fmt)
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# Classification: PUBLIC
# ---------------------------------------------------------------------------

class TestPublicExports:
    """Public records: all formats allowed, no restrictions."""

    @pytest.mark.parametrize("fmt", list(ExportFormat))
    def test_public_all_formats_allowed(self, fmt: ExportFormat) -> None:
        decision = check_export_allowed(ClassificationLevel.PUBLIC, fmt)
        assert decision.allowed is True
        assert decision.watermark_required is False
        assert decision.approval_required is False


# ---------------------------------------------------------------------------
# Custom policy matrix
# ---------------------------------------------------------------------------

class TestCustomPolicyMatrix:
    """ExportPolicyMatrix can be customised; custom policies are enforced."""

    def test_custom_matrix_overrides_internal(self) -> None:
        matrix = ExportPolicyMatrix()
        # Lock down internal to JSON-only + watermark
        matrix.policies[ClassificationLevel.INTERNAL] = ExportPolicy(
            allowed_export_formats=[ExportFormat.JSON],
            watermark_required=True,
            approval_required=False,
        )
        assert check_export_allowed(
            ClassificationLevel.INTERNAL, ExportFormat.JSON, matrix=matrix
        ).allowed is True
        assert check_export_allowed(
            ClassificationLevel.INTERNAL, ExportFormat.CSV, matrix=matrix
        ).allowed is False

    def test_missing_level_returns_most_restrictive(self) -> None:
        """A matrix missing a level returns deny-all (safe default)."""
        matrix = ExportPolicyMatrix(policies={})
        decision = check_export_allowed(
            ClassificationLevel.PUBLIC, ExportFormat.JSON, matrix=matrix
        )
        assert decision.allowed is False

    def test_version_field_preserved(self) -> None:
        matrix = ExportPolicyMatrix(version=42)
        assert matrix.version == 42


# ---------------------------------------------------------------------------
# ExportDecision model
# ---------------------------------------------------------------------------

class TestExportDecision:
    def test_decision_allowed_fields(self) -> None:
        d = ExportDecision(allowed=True, reason="ok")
        assert d.allowed is True
        assert d.watermark_required is False
        assert d.approval_required is False

    def test_decision_denied_fields(self) -> None:
        d = ExportDecision(
            allowed=False,
            reason="denied",
            watermark_required=True,
            approval_required=True,
        )
        assert d.allowed is False
        assert d.watermark_required is True
        assert d.approval_required is True
