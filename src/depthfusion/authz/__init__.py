"""DepthFusion V2 Authorization — ACL enforcement, frontmatter parsing, RBAC, export controls."""
# export_controls.py uses pydantic — deferred so that importing depthfusion.authz.*
# submodules (e.g. authz.classification, authz.roles) works in stdio/local mode
# without pydantic installed.
_LAZY: dict[str, str] = {
    "ClassificationLevel": "depthfusion.authz.export_controls",
    "ExportDecision": "depthfusion.authz.export_controls",
    "ExportFormat": "depthfusion.authz.export_controls",
    "ExportPolicy": "depthfusion.authz.export_controls",
    "ExportPolicyMatrix": "depthfusion.authz.export_controls",
    "check_export_allowed": "depthfusion.authz.export_controls",
}


def __getattr__(name: str) -> object:  # noqa: ANN401
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    mod = importlib.import_module(module_path)
    obj = getattr(mod, name)
    globals()[name] = obj
    return obj


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
    "PolicyDecision",
    "PolicyEngine",
    "get_policy_engine",
    "SignedPolicySnapshot",
    "SnapshotVerification",
    "sign_policy_snapshot",
    "verify_policy_snapshot",
]
