"""DepthFusion V2 Authorization — ACL enforcement, frontmatter parsing, RBAC, export controls."""
from depthfusion.authz.export_controls import (
    ClassificationLevel,
    ExportDecision,
    ExportFormat,
    ExportPolicy,
    ExportPolicyMatrix,
    check_export_allowed,
)

from .capability_check import AuthorizationError, require_capability
from .frontmatter import ACLFrontmatter, parse_acl, write_acl
from .policy_engine import PolicyDecision, PolicyEngine, get_policy_engine
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
    "PolicyDecision",
    "PolicyEngine",
    "get_policy_engine",
]
