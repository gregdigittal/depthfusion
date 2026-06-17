"""Offline cache schema + ACL/classification admission filter (E-58 S-188).

This module is the Python-side authority for *what may be cached offline*. The
encrypted SQLCipher store itself lives in the Tauri Rust core
(`app/src-tauri/src/cache/`); this module mirrors that store's schema and
implements the admission policy (T-650) and the on-open tamper check
(T-651) in a form that is unit-testable without the Rust toolchain.

Design rules
------------
* **Schema parity** — :data:`CACHE_SCHEMA` mirrors the record + chunk +
  embedding subset that the Rust core persists, with the
  ACL / classification / lease columns required by S-188 AC-2.
* **Admission policy (T-650)** — :func:`is_admissible` admits a record *only*
  when the principal is in the record's ``acl_allow`` set **AND** the record's
  classification is within the principal's offline ceiling
  (``classification <= ceiling``, inclusive). This is default-deny: any record
  failing either test is rejected.
* **Tamper detection (T-651)** — :func:`compute_integrity_hmac` produces an
  HMAC-SHA256 over the schema DDL + the lease table contents. On open, a
  mismatch against the stored digest means the on-disk cache has been tampered
  with; callers must wipe + re-sync (see :func:`verify_on_open`).
* No plaintext secrets are logged; the HMAC key is supplied by the caller
  (wrapped by the OS keychain in production).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from depthfusion.authz.classification import (
    ClassificationLevel,
    is_within_ceiling,
)

__all__ = [
    "CACHE_SCHEMA",
    "CACHE_SCHEMA_VERSION",
    "CacheableRecord",
    "AdmissionDecision",
    "LeaseRow",
    "is_admissible",
    "filter_admissible",
    "compute_integrity_hmac",
    "verify_on_open",
    "TamperResult",
]


# ---------------------------------------------------------------------------
# Schema (mirrors the Rust SQLCipher store)
# ---------------------------------------------------------------------------
# The Rust core (app/src-tauri/src/cache/schema.rs) owns the authoritative
# DDL; this string MUST stay byte-for-byte in sync with it because the tamper
# HMAC is computed over the DDL text. Any schema change is a deliberate,
# coordinated edit on both sides plus a CACHE_SCHEMA_VERSION bump.
# ---------------------------------------------------------------------------

CACHE_SCHEMA_VERSION: int = 1

CACHE_SCHEMA: str = (
    "CREATE TABLE IF NOT EXISTS cached_record ("
    "record_id TEXT PRIMARY KEY, "
    "principal_id TEXT NOT NULL, "
    "classification TEXT NOT NULL, "
    "acl_allow TEXT NOT NULL, "
    "lease_expires_at INTEGER NOT NULL, "
    "content BLOB"
    ");\n"
    "CREATE TABLE IF NOT EXISTS cached_chunk ("
    "chunk_id TEXT PRIMARY KEY, "
    "record_id TEXT NOT NULL REFERENCES cached_record(record_id) ON DELETE CASCADE, "
    "ordinal INTEGER NOT NULL, "
    "text BLOB"
    ");\n"
    "CREATE TABLE IF NOT EXISTS cached_embedding ("
    "chunk_id TEXT PRIMARY KEY REFERENCES cached_chunk(chunk_id) ON DELETE CASCADE, "
    "dim INTEGER NOT NULL, "
    "vector BLOB"
    ");\n"
    "CREATE TABLE IF NOT EXISTS cache_lease ("
    "record_id TEXT PRIMARY KEY REFERENCES cached_record(record_id) ON DELETE CASCADE, "
    "issued_at INTEGER NOT NULL, "
    "expires_at INTEGER NOT NULL, "
    "classification TEXT NOT NULL"
    ");"
)


# ---------------------------------------------------------------------------
# Candidate record + admission decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheableRecord:
    """A candidate record being considered for offline caching.

    Attributes
    ----------
    record_id:
        Stable identifier for the record.
    classification:
        The record's data classification level.
    acl_allow:
        The set of principal IDs explicitly permitted to read this record.
        Admission requires the requesting principal to be a member.
    """

    record_id: str
    classification: ClassificationLevel
    acl_allow: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def of(
        record_id: str,
        classification: ClassificationLevel,
        acl_allow: Iterable[str],
    ) -> "CacheableRecord":
        """Convenience constructor that coerces ``acl_allow`` to a frozenset."""
        return CacheableRecord(
            record_id=record_id,
            classification=classification,
            acl_allow=frozenset(acl_allow),
        )


class _Reason(str, Enum):
    ADMITTED = "admitted"
    ACL_DENIED = "acl_denied"
    CEILING_EXCEEDED = "ceiling_exceeded"


@dataclass(frozen=True)
class AdmissionDecision:
    """Outcome of an admission check for one record."""

    admitted: bool
    reason: str

    @property
    def acl_denied(self) -> bool:
        return self.reason == _Reason.ACL_DENIED.value

    @property
    def ceiling_exceeded(self) -> bool:
        return self.reason == _Reason.CEILING_EXCEEDED.value


def is_admissible(
    record: CacheableRecord,
    principal_id: str,
    offline_ceiling: ClassificationLevel,
) -> AdmissionDecision:
    """Decide whether *record* may be cached offline for *principal_id*.

    Default-deny: the record is admitted **only** when *both* hold:

    1. ``principal_id in record.acl_allow`` (ACL membership), and
    2. ``record.classification`` is within *offline_ceiling* (inclusive).

    The ACL test is evaluated first so an out-of-ACL principal can never learn
    anything about the record's classification from the decision path.
    """
    if principal_id not in record.acl_allow:
        return AdmissionDecision(admitted=False, reason=_Reason.ACL_DENIED.value)
    if not is_within_ceiling(record.classification, offline_ceiling):
        return AdmissionDecision(
            admitted=False, reason=_Reason.CEILING_EXCEEDED.value
        )
    return AdmissionDecision(admitted=True, reason=_Reason.ADMITTED.value)


def filter_admissible(
    records: Sequence[CacheableRecord],
    principal_id: str,
    offline_ceiling: ClassificationLevel,
) -> list[CacheableRecord]:
    """Return only the records admissible for *principal_id* under *ceiling*.

    Order-preserving; never raises on an empty input.
    """
    return [
        r
        for r in records
        if is_admissible(r, principal_id, offline_ceiling).admitted
    ]


# ---------------------------------------------------------------------------
# Tamper detection (HMAC over schema + lease table)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaseRow:
    """A row of the lease table, in canonical (record_id-sorted) form."""

    record_id: str
    issued_at: int
    expires_at: int
    classification: ClassificationLevel


def _canonical_lease_bytes(leases: Sequence[LeaseRow]) -> bytes:
    """Serialise the lease table deterministically for HMAC input.

    Rows are sorted by ``record_id`` so the digest is independent of row
    insertion order — a reorder is not a tamper, a value change is.
    """
    parts: list[str] = []
    for row in sorted(leases, key=lambda r: r.record_id):
        parts.append(
            f"{row.record_id}|{row.issued_at}|{row.expires_at}|"
            f"{row.classification.value}"
        )
    return "\n".join(parts).encode("utf-8")


def compute_integrity_hmac(
    key: bytes,
    leases: Sequence[LeaseRow],
    schema: str = CACHE_SCHEMA,
    schema_version: int = CACHE_SCHEMA_VERSION,
) -> str:
    """Compute the integrity HMAC-SHA256 over the schema + lease table.

    The digest binds the schema DDL (so a column added/removed off-app is
    detected) and the full lease table (so a lease's expiry cannot be silently
    extended on disk). Returned as a hex string for stable storage.
    """
    mac = hmac.new(key, digestmod=hashlib.sha256)
    mac.update(str(schema_version).encode("utf-8"))
    mac.update(b"\x00")
    mac.update(schema.encode("utf-8"))
    mac.update(b"\x00")
    mac.update(_canonical_lease_bytes(leases))
    return mac.hexdigest()


class TamperResult(str, Enum):
    """Result of the on-open integrity check."""

    OK = "ok"
    """Digest matched — cache is intact, proceed to use it."""

    WIPE_AND_RESYNC = "wipe_and_resync"
    """Digest mismatch (or missing) — cache is untrusted; wipe + re-sync."""


def verify_on_open(
    key: bytes,
    stored_digest: Optional[str],
    leases: Sequence[LeaseRow],
    schema: str = CACHE_SCHEMA,
    schema_version: int = CACHE_SCHEMA_VERSION,
) -> TamperResult:
    """Verify the on-disk cache integrity at open time.

    Returns :attr:`TamperResult.OK` only when *stored_digest* is present and
    matches a freshly-computed HMAC over the current schema + lease table.
    Any mismatch — or a missing digest (first-open / cleared) — yields
    :attr:`TamperResult.WIPE_AND_RESYNC`, the signal for the caller to drop the
    cache file and re-sync from the server.

    The comparison uses :func:`hmac.compare_digest` to avoid timing leaks.
    """
    if not stored_digest:
        return TamperResult.WIPE_AND_RESYNC
    expected = compute_integrity_hmac(key, leases, schema, schema_version)
    if hmac.compare_digest(expected, stored_digest):
        return TamperResult.OK
    return TamperResult.WIPE_AND_RESYNC
