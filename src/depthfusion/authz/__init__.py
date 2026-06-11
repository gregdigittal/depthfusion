"""DepthFusion V2 Authorization — ACL enforcement, frontmatter parsing, and RBAC roles."""
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
    "parse_acl",
    "write_acl",
    "Capability",
    "Role",
    "ROLE_CAPABILITIES",
    "RoleStore",
    "has_capability",
]
