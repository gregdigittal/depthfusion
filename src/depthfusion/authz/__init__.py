"""DepthFusion V2 Authorization — ACL enforcement, frontmatter parsing, RBAC roles, and export controls."""
from .capability_check import AuthorizationError, require_capability
from .frontmatter import ACLFrontmatter, parse_acl, write_acl
from .roles import (
    Capability,
    Role,
    ROLE_CAPABILITIES,
    RoleStore,
    has_capability,
)
from depthfusion.authz.export_controls import (
    ClassificationLevel,
    ExportFormat,
    ExportPolicy,
    ExportPolicyMatrix,
    ExportDecision,
    check_export_allowed,
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
]
