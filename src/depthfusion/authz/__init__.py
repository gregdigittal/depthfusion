"""DepthFusion V2 Authorization — ACL enforcement, frontmatter parsing, and RBAC roles."""
from .capability_check import AuthorizationError, require_capability
from .frontmatter import ACLFrontmatter, parse_acl, write_acl
from .roles import (
    Capability,
    Role,
    ROLE_CAPABILITIES,
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
]
