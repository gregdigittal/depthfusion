"""Offline cache lease lifecycle: issuance, renewal, purge + offline query.

E-58 S-190 (T-657 / T-658 / T-659 / T-660).

This module is the Python-side authority for the *lifecycle* of an offline
cache lease — distinct from :mod:`depthfusion.cache.admission` (which decides
*whether a record may be cached*) and
:mod:`depthfusion.identity.device_lease` (which governs the *device
credential* lease, not per-record cache leases).

Responsibilities
----------------
* **Lease issuance / renewal (T-657)** — :class:`LeaseManager` issues a lease
  on a cached record with a classification-scaled TTL (default 7 days;
  ``confidential`` = 48 h; ``restricted`` = 24 h). Renewal extends the expiry
  from the renewal instant. A renewal that is *denied* (server refuses, e.g.
  because the device was revoked) triggers a full cache + token wipe.
* **Purge engine (T-658)** — :class:`PurgeEngine` removes expired leases (and
  their backing cache records). It runs on three triggers: application start
  (:meth:`PurgeEngine.run_on_start`), a background timer
  (:meth:`PurgeEngine.run_on_timer`), and a server revoke signal
  (:meth:`PurgeEngine.run_on_revoke`). A revoke purges *everything* and wipes
  the device token.
* **Offline query engine (T-659)** — :class:`OfflineQueryEngine` runs BM25 +
  vector search over the *cached* (unexpired) subset of records, returning an
  :class:`OfflineResultSet` that always carries the ``offline_subset`` flag so
  the UI can render the "offline subset" indicator.

Security rules
--------------
* No plaintext secrets are logged.
* Clock-rollback tamper: leases are evaluated against ``now``; a rolled-back
  clock cannot revive an expired lease because :class:`PurgeEngine` is also
  driven by a stored ``high_water_mark`` (the latest time the app has ever
  observed). If ``now`` is earlier than the high-water mark, the engine treats
  the clock as tampered and refuses to *extend* lease life — it evaluates
  expiry against ``max(now, high_water_mark)``.
* Renewal-denied and revoke both perform a *full* wipe — cache records and the
  device token together — so a departed employee's offline data cannot survive.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol, Sequence

from depthfusion.authz.classification import ClassificationLevel

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "CLASSIFICATION_TTL_SECONDS",
    "ttl_for_classification",
    "Lease",
    "LeaseStatus",
    "RenewalOutcome",
    "RenewalDeniedError",
    "RevokedError",
    "LeaseStore",
    "InMemoryLeaseStore",
    "TokenWiper",
    "CacheWiper",
    "LeaseManager",
    "PurgeTrigger",
    "PurgeResult",
    "PurgeEngine",
    "OfflineDocument",
    "OfflineResult",
    "OfflineResultSet",
    "OfflineQueryEngine",
]


# ---------------------------------------------------------------------------
# Classification-scaled TTLs (T-657)
# ---------------------------------------------------------------------------

# Default lease: 7 days. Confidential records get a much shorter 48h window;
# restricted records get 24h. Public/internal use the default.
DEFAULT_LEASE_SECONDS: int = 7 * 24 * 3600  # 7 days

CLASSIFICATION_TTL_SECONDS: dict[ClassificationLevel, int] = {
    ClassificationLevel.PUBLIC: DEFAULT_LEASE_SECONDS,
    ClassificationLevel.INTERNAL: DEFAULT_LEASE_SECONDS,
    ClassificationLevel.CONFIDENTIAL: 48 * 3600,  # 48 hours
    ClassificationLevel.RESTRICTED: 24 * 3600,  # 24 hours
}


def ttl_for_classification(classification: ClassificationLevel) -> int:
    """Return the lease TTL in seconds for *classification*.

    Falls back to :data:`DEFAULT_LEASE_SECONDS` for any level not explicitly
    mapped (never returns a *longer* TTL than the default — unknown levels are
    treated conservatively as the default, not extended).
    """
    return CLASSIFICATION_TTL_SECONDS.get(classification, DEFAULT_LEASE_SECONDS)


# ---------------------------------------------------------------------------
# Lease value object + status
# ---------------------------------------------------------------------------


class LeaseStatus(str, Enum):
    """Validity of a lease at a given instant."""

    VALID = "valid"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Lease:
    """A per-record offline cache lease.

    Attributes
    ----------
    record_id:
        The cached record this lease governs.
    classification:
        The record's data classification (drives the TTL).
    issued_at:
        Unix timestamp (seconds) when the lease was issued.
    expires_at:
        Unix timestamp (seconds) when the lease expires.
    """

    record_id: str
    classification: ClassificationLevel
    issued_at: float
    expires_at: float

    def status(self, now: float) -> LeaseStatus:
        """Return :attr:`LeaseStatus.VALID` iff *now* is before ``expires_at``."""
        return LeaseStatus.VALID if now < self.expires_at else LeaseStatus.EXPIRED

    def is_valid(self, now: float) -> bool:
        """Convenience: ``True`` iff the lease has not yet expired at *now*."""
        return self.status(now) is LeaseStatus.VALID


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RenewalDeniedError(RuntimeError):
    """Raised when the server denies a lease renewal (triggers a full wipe)."""


class RevokedError(RuntimeError):
    """Raised when the server returns a device-revoke signal (full wipe)."""


# ---------------------------------------------------------------------------
# Storage + collaborator protocols
# ---------------------------------------------------------------------------


class LeaseStore(Protocol):
    """Persistence interface for leases.

    The production implementation is backed by the encrypted SQLCipher cache;
    :class:`InMemoryLeaseStore` is the unit-test double.
    """

    def upsert(self, lease: Lease) -> None: ...

    def get(self, record_id: str) -> Optional[Lease]: ...

    def all_leases(self) -> list[Lease]: ...

    def delete(self, record_id: str) -> None: ...

    def clear(self) -> None: ...


class TokenWiper(Protocol):
    """Wipes the device credential / token on revoke or renewal-denied."""

    def wipe_token(self) -> None: ...


class CacheWiper(Protocol):
    """Removes cached record(s) from the encrypted store."""

    def wipe_record(self, record_id: str) -> None: ...

    def wipe_all(self) -> None: ...


# ---------------------------------------------------------------------------
# In-memory lease store (test double / reference impl)
# ---------------------------------------------------------------------------


class InMemoryLeaseStore:
    """A simple dict-backed :class:`LeaseStore` for tests and offline reasoning."""

    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}

    def upsert(self, lease: Lease) -> None:
        self._leases[lease.record_id] = lease

    def get(self, record_id: str) -> Optional[Lease]:
        return self._leases.get(record_id)

    def all_leases(self) -> list[Lease]:
        return list(self._leases.values())

    def delete(self, record_id: str) -> None:
        self._leases.pop(record_id, None)

    def clear(self) -> None:
        self._leases.clear()

    def __len__(self) -> int:
        return len(self._leases)


# ---------------------------------------------------------------------------
# Lease issuance / renewal (T-657)
# ---------------------------------------------------------------------------


@dataclass
class RenewalOutcome:
    """Result of a renewal attempt."""

    renewed: bool
    lease: Optional[Lease]
    reason: str = ""


class LeaseManager:
    """Issues and renews per-record cache leases with classification TTLs.

    Parameters
    ----------
    store:
        The lease persistence layer.
    cache_wiper:
        Used to wipe backing cache records on renewal-denied / revoke.
    token_wiper:
        Used to wipe the device token on renewal-denied / revoke.
    time_fn:
        Returns the current Unix time. Injected for deterministic testing.
    """

    def __init__(
        self,
        store: LeaseStore,
        cache_wiper: CacheWiper,
        token_wiper: TokenWiper,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._cache = cache_wiper
        self._token = token_wiper
        self._time = time_fn

    # -- issuance -------------------------------------------------------------

    def issue(
        self,
        record_id: str,
        classification: ClassificationLevel,
        now: Optional[float] = None,
    ) -> Lease:
        """Issue a fresh lease for *record_id*, scaled by *classification*.

        The lease window runs ``[now, now + ttl_for_classification(...)]``.
        Persists the lease via the store and returns it.
        """
        issued = now if now is not None else self._time()
        ttl = ttl_for_classification(classification)
        lease = Lease(
            record_id=record_id,
            classification=classification,
            issued_at=issued,
            expires_at=issued + ttl,
        )
        self._store.upsert(lease)
        return lease

    # -- renewal --------------------------------------------------------------

    def renew(
        self,
        record_id: str,
        *,
        server_grants: bool,
        now: Optional[float] = None,
    ) -> RenewalOutcome:
        """Attempt to renew the lease for *record_id*.

        Renewal piggybacks on an authenticated server contact. *server_grants*
        is the server's decision:

        * ``True``  → the lease is extended from *now* using the record's
          classification-scaled TTL; the new lease is persisted and returned.
        * ``False`` → the renewal is **denied**. Per S-190 AC-2 this triggers a
          full wipe of the backing cache record **and** the device token, and a
          :class:`RenewalDeniedError` is raised so the caller cannot proceed as
          if the lease were still valid.

        A renewal for an unknown ``record_id`` is treated as denied (there is
        nothing to renew → the safe action is to wipe).
        """
        moment = now if now is not None else self._time()

        if not server_grants:
            # Renewal denied → full wipe (cache record + token).
            logger.warning(
                "Lease renewal denied for record %s — wiping cache + token",
                record_id,
            )
            self._store.delete(record_id)
            self._cache.wipe_record(record_id)
            self._token.wipe_token()
            raise RenewalDeniedError(
                f"renewal denied for record {record_id}; cache + token wiped"
            )

        existing = self._store.get(record_id)
        if existing is None:
            # Nothing to renew — deny safely (wipe token to be conservative).
            logger.warning(
                "Renewal requested for unknown lease %s — denying + wiping token",
                record_id,
            )
            self._cache.wipe_record(record_id)
            self._token.wipe_token()
            raise RenewalDeniedError(
                f"no lease to renew for record {record_id}; wiped"
            )

        ttl = ttl_for_classification(existing.classification)
        renewed = Lease(
            record_id=record_id,
            classification=existing.classification,
            issued_at=moment,
            expires_at=moment + ttl,
        )
        self._store.upsert(renewed)
        return RenewalOutcome(renewed=True, lease=renewed, reason="granted")


# ---------------------------------------------------------------------------
# Purge engine (T-658)
# ---------------------------------------------------------------------------


class PurgeTrigger(str, Enum):
    """What caused a purge run."""

    STARTUP = "startup"
    TIMER = "timer"
    REVOKE = "revoke"


@dataclass
class PurgeResult:
    """Outcome of a purge run."""

    trigger: PurgeTrigger
    purged_record_ids: list[str] = field(default_factory=list)
    full_wipe: bool = False
    clock_tamper_detected: bool = False

    @property
    def purged_count(self) -> int:
        return len(self.purged_record_ids)


class PurgeEngine:
    """Removes expired leases (and backing records) on three triggers.

    The engine maintains a monotonic ``high_water_mark`` — the latest time it
    has ever evaluated. Each run evaluates lease expiry against
    ``effective_now = max(now, high_water_mark)``. This defeats a
    *clock-rollback tamper*: setting the system clock backwards cannot revive an
    expired lease, because the effective evaluation time never moves backwards.

    Parameters
    ----------
    store:
        The lease store.
    cache_wiper:
        Wipes backing cache records (per-record on expiry, all on revoke).
    token_wiper:
        Wipes the device token on a revoke.
    time_fn:
        Current Unix time provider (injected for testing).
    """

    def __init__(
        self,
        store: LeaseStore,
        cache_wiper: CacheWiper,
        token_wiper: TokenWiper,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._cache = cache_wiper
        self._token = token_wiper
        self._time = time_fn
        self._high_water_mark: float = 0.0

    @property
    def high_water_mark(self) -> float:
        """Latest time the engine has ever observed (anti-rollback anchor)."""
        return self._high_water_mark

    def _effective_now(self, now: Optional[float]) -> tuple[float, bool]:
        """Return ``(effective_now, tamper_detected)``.

        ``effective_now`` never goes backwards relative to the high-water mark.
        ``tamper_detected`` is ``True`` when the supplied wall-clock *now* is
        earlier than the high-water mark (a clock rollback).
        """
        wall = now if now is not None else self._time()
        tamper = wall < self._high_water_mark
        effective = max(wall, self._high_water_mark)
        # Advance the high-water mark to the effective time.
        self._high_water_mark = effective
        return effective, tamper

    def _purge_expired(self, effective_now: float) -> list[str]:
        purged: list[str] = []
        for lease in self._store.all_leases():
            if lease.status(effective_now) is LeaseStatus.EXPIRED:
                self._store.delete(lease.record_id)
                self._cache.wipe_record(lease.record_id)
                purged.append(lease.record_id)
        if purged:
            logger.info("Purged %d expired lease(s)", len(purged))
        return purged

    def run_on_start(self, now: Optional[float] = None) -> PurgeResult:
        """Purge expired leases at application start."""
        effective, tamper = self._effective_now(now)
        purged = self._purge_expired(effective)
        return PurgeResult(
            trigger=PurgeTrigger.STARTUP,
            purged_record_ids=purged,
            clock_tamper_detected=tamper,
        )

    def run_on_timer(self, now: Optional[float] = None) -> PurgeResult:
        """Purge expired leases on a background-timer tick."""
        effective, tamper = self._effective_now(now)
        purged = self._purge_expired(effective)
        return PurgeResult(
            trigger=PurgeTrigger.TIMER,
            purged_record_ids=purged,
            clock_tamper_detected=tamper,
        )

    def run_on_revoke(self, now: Optional[float] = None) -> PurgeResult:
        """Handle a server device-revoke signal: full cache + token wipe.

        Per S-190 AC-3, an admin revoke means *everything* offline must go: all
        leases, all cached records, and the device token. Offline devices that
        never receive this signal still die at lease expiry via the timer/start
        purges.
        """
        effective, tamper = self._effective_now(now)
        purged = [lease.record_id for lease in self._store.all_leases()]
        self._store.clear()
        self._cache.wipe_all()
        self._token.wipe_token()
        logger.warning(
            "Device revoke received — wiped %d cached record(s) + token",
            len(purged),
        )
        return PurgeResult(
            trigger=PurgeTrigger.REVOKE,
            purged_record_ids=purged,
            full_wipe=True,
            clock_tamper_detected=tamper,
        )


# ---------------------------------------------------------------------------
# Offline query engine (T-659)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OfflineDocument:
    """A cached document available for offline search.

    Attributes
    ----------
    record_id:
        The cached record id (must have a valid lease to be searchable).
    text:
        The cached plaintext used for BM25 lexical scoring.
    embedding:
        The cached embedding vector used for cosine similarity scoring. May be
        empty if no embedding was cached for this record.
    """

    record_id: str
    text: str
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class OfflineResult:
    """A single scored offline search result."""

    record_id: str
    score: float
    bm25_score: float
    vector_score: float


@dataclass(frozen=True)
class OfflineResultSet:
    """Offline search results plus the mandatory subset indicator.

    Attributes
    ----------
    results:
        Scored results, highest score first.
    offline_subset:
        Always ``True`` for an offline query — the UI renders an "offline
        subset" indicator from this flag so users know results are limited to
        the cached subset, not the full corpus.
    total_cached:
        Number of records with a *valid* lease at query time (the size of the
        searchable offline subset).
    indicator_label:
        Human-readable label for the UI badge.
    """

    results: list[OfflineResult]
    offline_subset: bool = True
    total_cached: int = 0
    indicator_label: str = "Offline subset — showing cached results only"


def _tokenize(text: str) -> list[str]:
    """Lowercase whitespace tokenizer (kept deliberately simple + offline)."""
    return [tok for tok in text.lower().split() if tok]


def _bm25_scores(
    query_tokens: Sequence[str],
    docs: Sequence[OfflineDocument],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[str, float]:
    """Compute BM25 scores for *docs* against *query_tokens*.

    A compact, dependency-free BM25 over the cached subset. Returns a mapping
    of ``record_id -> score`` (0.0 for docs with no query-term overlap).
    """
    import math

    n = len(docs)
    if n == 0:
        return {}

    tokenized: dict[str, list[str]] = {d.record_id: _tokenize(d.text) for d in docs}
    doc_lengths = {rid: len(toks) for rid, toks in tokenized.items()}
    avgdl = (sum(doc_lengths.values()) / n) if n else 0.0

    # Document frequency per query term.
    query_set = set(query_tokens)
    df: dict[str, int] = {term: 0 for term in query_set}
    for toks in tokenized.values():
        present = set(toks)
        for term in query_set:
            if term in present:
                df[term] += 1

    scores: dict[str, float] = {}
    for rid, toks in tokenized.items():
        dl = doc_lengths[rid]
        score = 0.0
        for term in query_tokens:
            f = toks.count(term)
            if f == 0:
                continue
            n_q = df.get(term, 0)
            # BM25 idf with +1 to keep it non-negative.
            idf = math.log(1 + (n - n_q + 0.5) / (n_q + 0.5))
            denom = f + k1 * (1 - b + b * (dl / avgdl if avgdl else 0.0))
            score += idf * (f * (k1 + 1)) / denom if denom else 0.0
        scores[rid] = score
    return scores


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in ``[0, 1]`` (0.0 if either vector is empty/zero)."""
    import math

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    sim = dot / (na * nb)
    # Clamp into [0, 1]; cosine can be negative for opposed vectors.
    return max(0.0, min(1.0, sim))


class OfflineQueryEngine:
    """BM25 + vector search over the *unexpired cached subset*.

    The engine only searches records whose lease is currently valid — expired
    records are excluded even if the purge engine has not yet swept them, so a
    stale-but-not-yet-purged record can never be served offline.

    Parameters
    ----------
    store:
        The lease store (used to determine which records have a valid lease).
    documents:
        The cached documents (text + embedding) keyed implicitly by
        ``record_id``. In production these are read from the encrypted cache.
    alpha:
        Blend weight for vector vs BM25: ``score = alpha*vector + (1-alpha)*bm25``
        after each component is min-max normalised within the result set.
    time_fn:
        Current Unix time provider.
    """

    def __init__(
        self,
        store: LeaseStore,
        documents: Sequence[OfflineDocument],
        *,
        alpha: float = 0.5,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._docs = list(documents)
        self._alpha = max(0.0, min(1.0, alpha))
        self._time = time_fn

    def _valid_subset(self, now: float) -> list[OfflineDocument]:
        """Documents whose lease is currently valid."""
        valid: list[OfflineDocument] = []
        for doc in self._docs:
            lease = self._store.get(doc.record_id)
            if lease is not None and lease.is_valid(now):
                valid.append(doc)
        return valid

    def search(
        self,
        query: str,
        query_embedding: Optional[Sequence[float]] = None,
        *,
        top_k: int = 10,
        now: Optional[float] = None,
    ) -> OfflineResultSet:
        """Run a hybrid BM25 + vector search over the cached subset.

        Always returns an :class:`OfflineResultSet` with ``offline_subset=True``
        so the UI renders the "offline subset" indicator. ``top_k`` caps the
        number of results returned.
        """
        moment = now if now is not None else self._time()
        subset = self._valid_subset(moment)

        if not subset:
            return OfflineResultSet(results=[], total_cached=0)

        query_tokens = _tokenize(query)
        bm25 = _bm25_scores(query_tokens, subset)

        vec: dict[str, float] = {}
        if query_embedding:
            for doc in subset:
                vec[doc.record_id] = _cosine(query_embedding, doc.embedding)
        else:
            vec = {doc.record_id: 0.0 for doc in subset}

        # Min-max normalise each component within the subset so the blend is
        # scale-invariant.
        bm25_norm = _min_max_normalise(bm25)
        vec_norm = _min_max_normalise(vec)

        results: list[OfflineResult] = []
        for doc in subset:
            b = bm25_norm.get(doc.record_id, 0.0)
            v = vec_norm.get(doc.record_id, 0.0)
            blended = self._alpha * v + (1.0 - self._alpha) * b
            results.append(
                OfflineResult(
                    record_id=doc.record_id,
                    score=blended,
                    bm25_score=bm25.get(doc.record_id, 0.0),
                    vector_score=vec.get(doc.record_id, 0.0),
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return OfflineResultSet(
            results=results[: max(0, top_k)],
            total_cached=len(subset),
        )


def _min_max_normalise(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalise *scores* into ``[0, 1]``.

    If all scores are equal (including all-zero), every entry maps to 0.0 so a
    component that carries no signal does not inflate the blended score.
    """
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    if hi <= lo:
        return {k: 0.0 for k in scores}
    span = hi - lo
    return {k: (v - lo) / span for k, v in scores.items()}
