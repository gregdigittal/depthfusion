"""Classification taxonomy and handling-rules policy module.

Defines:
- ``Role``               — role enum used in handling rules
- ``ClassificationLevel`` — data classification taxonomy
- ``HandlingRules``       — per-level policy as a typed dict
- ``CLASSIFICATION_POLICY`` — the authoritative policy mapping; default-deny on unknown

Design rules:
- All levels must be explicitly mapped; any unlisted level → deny (handled by
  ``get_handling_rules`` which raises ``KeyError`` on unknown labels).
- Policy is read-only at import time; consumers must not mutate it.
"""
from __future__ import annotations

from enum import Enum
from typing import TypedDict


class Role(str, Enum):
    """Roles that may appear in ``HandlingRules.allowed_roles``.

    Roles are expressed as strings so they can be compared directly against
    group/role claims carried in ``Principal.groups``.

    ``MEMBER`` aligns with the ``rbac.Role.MEMBER`` vocabulary so that standard
    team-member principals (``groups=["member"]``) are not denied INTERNAL-classified
    records they are explicitly listed in the ACL for (F-002).
    """

    ADMIN = "admin"
    DATA_ENGINEER = "data_engineer"
    ANALYST = "analyst"
    MEMBER = "member"
    VIEWER = "viewer"
    EXTERNAL = "external"


class ClassificationLevel(str, Enum):
    """Four-tier data classification taxonomy.

    Levels are ordered from least to most sensitive:
    ``public < internal < confidential < restricted``.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class HandlingRules(TypedDict):
    """Policy rules attached to each ``ClassificationLevel``.

    Attributes
    ----------
    export_allowed:
        Whether records at this level may be exported outside the platform.
    cache_allowed:
        Whether records at this level may be stored in intermediate caches
        (search caches, Redis, CDN, etc.).
    redact_in_search:
        Whether content at this level must be redacted / masked in search
        result snippets returned to lower-privilege principals.
    allowed_roles:
        Roles whose members may access records at this level.  Access is
        denied to any principal whose ``groups`` intersect with *none* of
        the listed roles.
    """

    export_allowed: bool
    cache_allowed: bool
    redact_in_search: bool
    allowed_roles: list[Role]


# ---------------------------------------------------------------------------
# Authoritative classification policy
# ---------------------------------------------------------------------------
# Default-deny contract:
#   - Only levels explicitly listed here are permitted.
#   - ``get_handling_rules()`` (below) raises ``KeyError`` for any label not
#     present in this mapping, so callers must treat unknown labels as deny.
# ---------------------------------------------------------------------------

CLASSIFICATION_POLICY: dict[ClassificationLevel, HandlingRules] = {
    ClassificationLevel.PUBLIC: HandlingRules(
        export_allowed=True,
        cache_allowed=True,
        redact_in_search=False,
        allowed_roles=[
            Role.ADMIN,
            Role.DATA_ENGINEER,
            Role.ANALYST,
            Role.VIEWER,
            Role.EXTERNAL,
        ],
    ),
    ClassificationLevel.INTERNAL: HandlingRules(
        export_allowed=False,
        cache_allowed=True,
        redact_in_search=False,
        allowed_roles=[
            Role.ADMIN,
            Role.DATA_ENGINEER,
            Role.ANALYST,
            Role.MEMBER,
            Role.VIEWER,
        ],
    ),
    ClassificationLevel.CONFIDENTIAL: HandlingRules(
        export_allowed=False,
        cache_allowed=False,
        redact_in_search=True,
        allowed_roles=[
            Role.ADMIN,
            Role.DATA_ENGINEER,
        ],
    ),
    ClassificationLevel.RESTRICTED: HandlingRules(
        export_allowed=False,
        cache_allowed=False,
        redact_in_search=True,
        allowed_roles=[
            Role.ADMIN,
        ],
    ),
}


# ---------------------------------------------------------------------------
# Classification ordering (least → most sensitive)
# ---------------------------------------------------------------------------
# Used for ceiling enforcement: a principal/service-account with ceiling C may
# see records whose level L satisfies ``level_rank(L) <= level_rank(C)``.
# ---------------------------------------------------------------------------

_LEVEL_ORDER: tuple[ClassificationLevel, ...] = (
    ClassificationLevel.PUBLIC,
    ClassificationLevel.INTERNAL,
    ClassificationLevel.CONFIDENTIAL,
    ClassificationLevel.RESTRICTED,
)

_LEVEL_RANK: dict[ClassificationLevel, int] = {
    level: idx for idx, level in enumerate(_LEVEL_ORDER)
}


def level_rank(level: ClassificationLevel) -> int:
    """Return the ordinal rank of *level* (0=public … 3=restricted).

    Raises ``KeyError`` for any level not in the taxonomy (default-deny).
    """
    return _LEVEL_RANK[level]


def is_within_ceiling(
    level: ClassificationLevel, ceiling: ClassificationLevel
) -> bool:
    """Return ``True`` iff a record at *level* is visible under *ceiling*.

    A ceiling is inclusive: ``ceiling=confidential`` admits public, internal,
    and confidential records, but excludes restricted.
    """
    return level_rank(level) <= level_rank(ceiling)


def get_handling_rules(level: ClassificationLevel) -> HandlingRules:
    """Return the ``HandlingRules`` for *level*.

    Parameters
    ----------
    level:
        A ``ClassificationLevel`` value.

    Returns
    -------
    HandlingRules
        The policy rules for the supplied classification level.

    Raises
    ------
    KeyError
        If *level* is not mapped in ``CLASSIFICATION_POLICY``.  This
        implements the default-deny contract — unknown labels are rejected,
        not allowed.
    """
    return CLASSIFICATION_POLICY[level]


__all__ = [
    "ClassificationLevel",
    "HandlingRules",
    "Role",
    "CLASSIFICATION_POLICY",
    "get_handling_rules",
    "level_rank",
    "is_within_ceiling",
]
