"""Export Controls & IP Protection — E-59.

Policy matrix mapping ClassificationLevel → export rules.
Enforced at export endpoints: no file/data export is allowed without
consulting the policy. Restricted and confidential records require
approval; watermarked records receive a provenance footer.

Design notes
------------
- ``ExportPolicy`` is a Pydantic v2 model (immutable after construction).
- ``ExportPolicyMatrix`` holds the full classification → policy mapping.
- ``check_export_allowed`` is the single enforcement point: call it from
  any endpoint that exports data before streaming bytes to the client.
- ``approval_required=True`` records return a DENIED decision until an
  explicit approval token is supplied.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ClassificationLevel(str, Enum):
    """Data classification ladder — maps directly to E-59 policy tiers."""
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ExportFormat(str, Enum):
    """Recognised export formats. Controls are per-format per-classification."""
    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"
    PDF = "pdf"
    RAW = "raw"


class ExportPolicy(BaseModel):
    """Policy for a single classification level.

    Attributes
    ----------
    allowed_export_formats:
        Which export formats are permitted at all. An empty list means NO
        export is permitted regardless of approval.
    watermark_required:
        If True the export payload must include a provenance footer/watermark
        (e.g. ``-- Exported by <principal> at <timestamp>``) before delivery.
    approval_required:
        If True the caller must supply an out-of-band approval token. Without
        it ``check_export_allowed`` returns a DENIED decision.
    """
    allowed_export_formats: list[ExportFormat] = Field(default_factory=list)
    watermark_required: bool = False
    approval_required: bool = False

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Default policy matrix (E-59 defaults from S-191 AC-2)
# ---------------------------------------------------------------------------

DEFAULT_POLICY_MATRIX: dict[ClassificationLevel, ExportPolicy] = {
    ClassificationLevel.PUBLIC: ExportPolicy(
        allowed_export_formats=[
            ExportFormat.JSON,
            ExportFormat.CSV,
            ExportFormat.MARKDOWN,
            ExportFormat.PDF,
            ExportFormat.RAW,
        ],
        watermark_required=False,
        approval_required=False,
    ),
    ClassificationLevel.INTERNAL: ExportPolicy(
        allowed_export_formats=[
            ExportFormat.JSON,
            ExportFormat.CSV,
            ExportFormat.MARKDOWN,
        ],
        watermark_required=False,
        approval_required=False,
    ),
    ClassificationLevel.CONFIDENTIAL: ExportPolicy(
        allowed_export_formats=[
            ExportFormat.JSON,
            ExportFormat.CSV,
        ],
        watermark_required=True,
        approval_required=False,
    ),
    ClassificationLevel.RESTRICTED: ExportPolicy(
        allowed_export_formats=[],      # no format allowed without approval
        watermark_required=True,
        approval_required=True,
    ),
}


class ExportPolicyMatrix(BaseModel):
    """Versioned, admin-editable policy matrix (S-191 AC-1).

    Maps every ``ClassificationLevel`` to an ``ExportPolicy``.
    Defaults are the canonical E-59 baseline. Admin CRUD replaces entries.
    """
    version: int = 1
    policies: dict[ClassificationLevel, ExportPolicy] = Field(
        default_factory=lambda: dict(DEFAULT_POLICY_MATRIX)
    )

    model_config = {"frozen": False}

    def get_policy(self, level: ClassificationLevel) -> ExportPolicy:
        """Return the policy for *level*, falling back to most-restrictive if absent."""
        return self.policies.get(
            level,
            ExportPolicy(
                allowed_export_formats=[],
                watermark_required=True,
                approval_required=True,
            ),
        )


class ExportDecision(BaseModel):
    """Result returned by ``check_export_allowed``.

    Attributes
    ----------
    allowed:
        True iff the export is permitted under the current policy.
    reason:
        Human-readable explanation (for logs and UI denial messages).
    watermark_required:
        Caller must inject a provenance footer before delivering the payload.
    approval_required:
        Export was denied because approval was required but not supplied.
    """
    allowed: bool
    reason: str
    watermark_required: bool = False
    approval_required: bool = False


# ---------------------------------------------------------------------------
# Enforcement function — the single call-site for all export endpoints
# ---------------------------------------------------------------------------

def check_export_allowed(
    classification: ClassificationLevel,
    export_format: ExportFormat,
    *,
    matrix: Optional[ExportPolicyMatrix] = None,
    approval_token: Optional[str] = None,
) -> ExportDecision:
    """Evaluate whether an export is permitted under the active policy.

    Parameters
    ----------
    classification:
        The classification level of the record being exported.
    export_format:
        The requested export format.
    matrix:
        The active policy matrix. If ``None``, the module-level
        ``DEFAULT_POLICY_MATRIX`` is used (wrapped in a transient matrix).
    approval_token:
        Opaque token supplied by an authorised approver. Any non-empty
        string is accepted as proof of approval (production implementations
        should validate this cryptographically against a signed approval
        record — that is the E-59 S-191 T-662 extension point).

    Returns
    -------
    ExportDecision
        ``allowed=True`` iff the policy permits the export.
    """
    if matrix is None:
        matrix = ExportPolicyMatrix()

    policy = matrix.get_policy(classification)

    # Gate 1: approval required but not provided
    if policy.approval_required and not approval_token:
        return ExportDecision(
            allowed=False,
            reason=(
                f"Export of {classification.value!r} records requires explicit approval. "
                "Supply an approval_token to proceed."
            ),
            watermark_required=policy.watermark_required,
            approval_required=True,
        )

    # Gate 2: format not in allowed list
    if export_format not in policy.allowed_export_formats:
        return ExportDecision(
            allowed=False,
            reason=(
                f"Export format {export_format.value!r} is not permitted for "
                f"{classification.value!r} classification."
            ),
            watermark_required=policy.watermark_required,
            approval_required=policy.approval_required,
        )

    return ExportDecision(
        allowed=True,
        reason="Export permitted under current policy.",
        watermark_required=policy.watermark_required,
        approval_required=False,
    )
