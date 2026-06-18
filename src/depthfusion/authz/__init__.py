"""DepthFusion V2 Authorization — ACL enforcement, frontmatter parsing, RBAC, export controls."""
from depthfusion.authz.export_audit import (
    AlertChannel,
    AnomalyAlert,
    AnomalyKind,
    ExportAnomalyDetector,
    ExportAuditResult,
    ExportRateLimiter,
    audit_export_action,
)
from depthfusion.authz.export_controls import (
    CONFIDENTIAL_FOOTER_THRESHOLD,
    ClassificationLevel,
    ExportDecision,
    ExportFormat,
    ExportPolicy,
    ExportPolicyMatrix,
    WatermarkPolicy,
    apply_provenance_footer,
    build_provenance_footer,
    check_export_allowed,
    classification_rank,
    get_watermark_policy,
)

from .capability_check import AuthorizationError, require_capability
from .frontmatter import ACLFrontmatter, parse_acl, write_acl
from .policy_engine import PolicyDecision, PolicyEngine, get_policy_engine
from .policy_snapshot import (
    SignedPolicySnapshot,
    SnapshotVerification,
    sign_policy_snapshot,
    verify_policy_snapshot,
)
from .roles import (
    ROLE_CAPABILITIES,
    Capability,
    Role,
    RoleStore,
    has_capability,
)

__all__ = [
    "ACLFrontmatter",
    "AuthorizationError",
    "parse_acl",
    "require_capability",
    "write_acl",
    "Capability",
    "Role",
    "ROLE_CAPABILITIES",
    "RoleStore",
    "has_capability",
    "ClassificationLevel",
    "ExportFormat",
    "ExportPolicy",
    "ExportPolicyMatrix",
    "ExportDecision",
    "check_export_allowed",
    "CONFIDENTIAL_FOOTER_THRESHOLD",
    "WatermarkPolicy",
    "apply_provenance_footer",
    "build_provenance_footer",
    "classification_rank",
    "get_watermark_policy",
    "AlertChannel",
    "AnomalyAlert",
    "AnomalyKind",
    "ExportAnomalyDetector",
    "ExportAuditResult",
    "ExportRateLimiter",
    "audit_export_action",
    "PolicyDecision",
    "PolicyEngine",
    "get_policy_engine",
    "SignedPolicySnapshot",
    "SnapshotVerification",
    "sign_policy_snapshot",
    "verify_policy_snapshot",
]
