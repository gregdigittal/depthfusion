"""Service-account issuance with classification-ceiling enforcement (T-624).

BI tools (Metabase / Grafana / Power BI) connect to DepthFusion through a
*service account* rather than an interactive user.  A service account is a
scoped, read-only principal that carries a **classification ceiling**: the
highest sensitivity level its bearer is permitted to see.

Two hard invariants (E-55 / E-59 epic rules):

1. **The ceiling is server-returned, never hardcoded.**  The issuer assigns
   the ceiling from policy at issuance time; downstream code reads it off the
   :class:`ServiceAccount` record and never substitutes a literal level.  The
   default issuance ceiling is the *least* sensitive level
   (``public``) — least-privilege by default — and may only be raised by an
   explicit, audited issuance request.

2. **Records above the ceiling are excluded.**  :func:`filter_records_by_ceiling`
   drops any record whose ``classification`` rank exceeds the account's
   ceiling.  A record with a missing or unknown classification is treated as
   *restricted* (default-deny) and excluded unless the ceiling is restricted.

The module is storage-light by design: issuance returns an immutable record
that the caller persists (e.g. alongside the API token) — there is no
ambient mutable global state to drift.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from depthfusion.authz.classification import (
    ClassificationLevel,
    is_within_ceiling,
)

#: The least-privilege default ceiling applied when an issuance request does
#: not specify one.  Public-only is the floor of the taxonomy.
DEFAULT_CEILING: ClassificationLevel = ClassificationLevel.PUBLIC


@dataclass(frozen=True)
class ServiceAccount:
    """An issued, scoped, read-only service principal.

    Attributes
    ----------
    account_id:
        Stable identifier for the service account (used as the principal_id
        for any events it generates).
    name:
        Human-readable label, e.g. ``"metabase-prod"``.
    ceiling:
        The classification ceiling — the highest level this account may read.
        **Server-assigned at issuance; never hardcoded downstream.**
    token:
        Opaque bearer token the BI tool presents.  Generated with
        :func:`secrets.token_urlsafe`; treat as a credential.
    issued_at:
        UTC ISO-8601 timestamp of issuance.
    scopes:
        Read scopes granted (e.g. ``["query:read"]``).  Service accounts are
        read-only — no write scope may be granted.
    """

    account_id: str
    name: str
    ceiling: ClassificationLevel
    token: str
    issued_at: str
    scopes: tuple[str, ...] = field(default_factory=tuple)


def _coerce_ceiling(ceiling: ClassificationLevel | str | None) -> ClassificationLevel:
    """Resolve a requested ceiling to a ``ClassificationLevel``.

    A ``None`` request yields the least-privilege :data:`DEFAULT_CEILING`.
    A string is parsed against the taxonomy; an unknown string raises
    ``ValueError`` (default-deny — never silently widen access).
    """
    if ceiling is None:
        return DEFAULT_CEILING
    if isinstance(ceiling, ClassificationLevel):
        return ceiling
    try:
        return ClassificationLevel(ceiling)
    except ValueError as exc:
        raise ValueError(
            f"Unknown classification ceiling {ceiling!r}; "
            f"allowed: {[lvl.value for lvl in ClassificationLevel]}"
        ) from exc


def issue_service_account(
    *,
    name: str,
    ceiling: ClassificationLevel | str | None = None,
    scopes: Iterable[str] = ("query:read",),
) -> ServiceAccount:
    """Issue a new read-only service account with a classification ceiling.

    Parameters
    ----------
    name:
        Human-readable label for the account (required, non-empty).
    ceiling:
        The classification ceiling to assign.  Defaults to the
        least-privilege :data:`DEFAULT_CEILING` (``public``) when omitted —
        the ceiling is policy-assigned here at issuance, never hardcoded by
        consumers.
    scopes:
        Read scopes to grant.  Any write-shaped scope is rejected — service
        accounts are strictly read-only.

    Returns
    -------
    ServiceAccount
        An immutable record the caller persists alongside the token.

    Raises
    ------
    ValueError
        If *name* is blank, *ceiling* is unknown, or a write scope is
        requested.
    """
    if not name or not name.strip():
        raise ValueError("service account name must be non-empty")

    resolved = _coerce_ceiling(ceiling)

    scope_tuple = tuple(scopes)
    _MUTATION_VERBS = {"write", "create", "update", "delete", "admin", "manage"}
    for scope in scope_tuple:
        # Read-only enforcement: reject anything that grants mutation.  Scopes
        # are colon-delimited (e.g. ``query:read`` or ``records:write``); a
        # mutation verb in *any* segment disqualifies the scope.
        segments = {seg.lower() for seg in scope.split(":")}
        if segments & _MUTATION_VERBS:
            raise ValueError(
                f"service accounts are read-only; rejected scope {scope!r}"
            )

    return ServiceAccount(
        account_id=f"svc-{secrets.token_hex(8)}",
        name=name.strip(),
        ceiling=resolved,
        token=secrets.token_urlsafe(32),
        issued_at=datetime.now(tz=timezone.utc).isoformat(),
        scopes=scope_tuple,
    )


def _record_level(record: Any) -> ClassificationLevel:
    """Extract a record's classification level, default-deny on absence.

    A record may be a mapping (``record["classification"]``) or an object
    (``record.classification``).  A missing / unknown / null classification
    is treated as ``restricted`` so it is excluded unless the ceiling is
    itself restricted — the safe default.
    """
    raw: Any = None
    if isinstance(record, dict):
        raw = record.get("classification")
    else:
        raw = getattr(record, "classification", None)

    if raw is None:
        return ClassificationLevel.RESTRICTED
    if isinstance(raw, ClassificationLevel):
        return raw
    try:
        return ClassificationLevel(str(raw).lower())
    except ValueError:
        return ClassificationLevel.RESTRICTED


def is_record_visible(record: Any, account: ServiceAccount) -> bool:
    """Return ``True`` iff *record* is at or below *account*'s ceiling.

    The ceiling is read from the account record — never a literal.
    """
    return is_within_ceiling(_record_level(record), account.ceiling)


def filter_records_by_ceiling(
    records: Iterable[Any], account: ServiceAccount
) -> list[Any]:
    """Return only the records visible under *account*'s classification ceiling.

    Records above the ceiling are excluded.  This is the enforcement point a
    service-account-backed query path calls before returning rows to a BI
    tool.
    """
    return [r for r in records if is_record_visible(r, account)]


__all__ = [
    "DEFAULT_CEILING",
    "ServiceAccount",
    "issue_service_account",
    "is_record_visible",
    "filter_records_by_ceiling",
]
