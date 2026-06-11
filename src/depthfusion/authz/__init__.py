"""DepthFusion V2 Authorization — ACL enforcement and frontmatter parsing."""
from .frontmatter import ACLFrontmatter, parse_acl, write_acl

__all__ = ["ACLFrontmatter", "parse_acl", "write_acl"]
