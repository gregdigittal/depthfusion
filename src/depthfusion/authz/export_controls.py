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

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ClassificationLevel(str, Enum):
    """Data classification ladder — maps directly to E-59 policy tiers.

    The members are ordered from least- to most-sensitive. Use
    :func:`classification_rank` to compare two levels; never compare the
    string values directly (they are not lexicographically ordered).
    """
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# Ordered ladder (ascending sensitivity). Index = rank.
_CLASSIFICATION_ORDER: tuple[ClassificationLevel, ...] = (
    ClassificationLevel.PUBLIC,
    ClassificationLevel.INTERNAL,
    ClassificationLevel.CONFIDENTIAL,
    ClassificationLevel.RESTRICTED,
)


def classification_rank(level: ClassificationLevel) -> int:
    """Return the ordinal rank of *level* on the sensitivity ladder.

    PUBLIC=0, INTERNAL=1, CONFIDENTIAL=2, RESTRICTED=3. Higher = more
    sensitive. Used for ``>=`` style threshold comparisons.
    """
    return _CLASSIFICATION_ORDER.index(level)


# Threshold at/above which a per-principal provenance footer is appended to
# copy-text payloads (T-665 / S-192).
CONFIDENTIAL_FOOTER_THRESHOLD: ClassificationLevel = ClassificationLevel.CONFIDENTIAL


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


# ---------------------------------------------------------------------------
# Provenance footer + watermark policy hooks (T-665 / S-192)
#
# When copy-text is allowed and a record's classification is at or above the
# CONFIDENTIAL threshold, a per-principal provenance footer is appended to the
# delivered text so any leaked copy can be traced back to the principal that
# exported it. Below the threshold no footer is added (avoids noise on
# public/internal copy operations).
#
# The watermark policy hook (``get_watermark_policy``) is consumed by the view
# layer to decide how to render an on-screen / overlay watermark for a given
# classification and principal.
# ---------------------------------------------------------------------------


class WatermarkPolicy(BaseModel):
    """Active watermark policy for a (classification, principal) pair.

    Returned by :func:`get_watermark_policy` and consumed by the view layer.

    Attributes
    ----------
    enabled:
        Whether a watermark should be rendered at all. True iff the underlying
        :class:`ExportPolicy` requires a watermark for this classification.
    principal_id:
        The principal the watermark is scoped to (per-principal traceability).
    classification:
        The classification level this policy applies to.
    label:
        Short human-readable label rendered in the watermark overlay, e.g.
        ``"CONFIDENTIAL — alice"``.
    """
    enabled: bool
    principal_id: str
    classification: ClassificationLevel
    label: str

    model_config = {"frozen": True}


def get_watermark_policy(
    classification: ClassificationLevel,
    principal_id: str,
    *,
    matrix: Optional[ExportPolicyMatrix] = None,
) -> WatermarkPolicy:
    """Return the active watermark policy for a classification/principal.

    This is the hook the view layer calls to decide whether (and how) to render
    an overlay watermark. ``enabled`` mirrors the ``watermark_required`` flag of
    the active :class:`ExportPolicy` for *classification*.

    Parameters
    ----------
    classification:
        Classification level of the record being viewed/exported.
    principal_id:
        Identifier of the principal the watermark is scoped to.
    matrix:
        Active policy matrix. Defaults to the canonical E-59 baseline.
    """
    if matrix is None:
        matrix = ExportPolicyMatrix()
    policy = matrix.get_policy(classification)
    label = f"{classification.value.upper()} — {principal_id}"
    return WatermarkPolicy(
        enabled=policy.watermark_required,
        principal_id=principal_id,
        classification=classification,
        label=label,
    )


def build_provenance_footer(
    principal_id: str,
    classification: ClassificationLevel,
    *,
    timestamp: Optional[datetime] = None,
) -> str:
    """Construct the per-principal provenance footer string.

    Always returns a non-empty footer — the caller is responsible for deciding
    whether to append it (use :func:`apply_provenance_footer`, which enforces
    the classification threshold).
    """
    ts = (timestamp or datetime.now(timezone.utc)).isoformat()
    return (
        "\n\n-- "
        f"Copied by {principal_id} | classification: {classification.value} "
        f"| at {ts}"
    )


def apply_provenance_footer(
    text: str,
    principal_id: str,
    classification: ClassificationLevel,
    *,
    copy_allowed: bool = True,
    timestamp: Optional[datetime] = None,
) -> str:
    """Append a per-principal provenance footer to *text* when warranted.

    The footer is appended ONLY when BOTH conditions hold:

    1. ``copy_allowed`` is True (the copy-text action is permitted), and
    2. ``classification`` is at or above the CONFIDENTIAL threshold.

    Below the CONFIDENTIAL threshold (PUBLIC, INTERNAL) the text is returned
    unchanged. If copy is not allowed the text is also returned unchanged
    (no footer leaks onto a payload that should not have been copied).

    Parameters
    ----------
    text:
        The copy-text payload about to be delivered to the principal.
    principal_id:
        Identifier of the principal performing the copy.
    classification:
        Classification level of the source record.
    copy_allowed:
        Whether the copy-text action itself was permitted by policy.
    timestamp:
        Optional fixed timestamp (UTC). Defaults to ``datetime.now(timezone.utc)``.

    Returns
    -------
    str
        ``text`` with the provenance footer appended, or ``text`` unchanged.
    """
    if not copy_allowed:
        return text
    if classification_rank(classification) < classification_rank(
        CONFIDENTIAL_FOOTER_THRESHOLD
    ):
        return text
    return text + build_provenance_footer(
        principal_id, classification, timestamp=timestamp
    )
