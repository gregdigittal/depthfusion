"""Intelligent Offline Cache — ML-driven, encrypted at rest.

E-58: LRU cache backed by SQLite with Fernet encryption.
"""

from depthfusion.cache.activity_signals import (
    _PRIVACY_GUARD,
    ActivitySignal,
    ActivitySignalStore,
    SignalKind,
)
from depthfusion.cache.admission import (
    CACHE_SCHEMA,
    CACHE_SCHEMA_VERSION,
    AdmissionDecision,
    CacheableRecord,
    LeaseRow,
    TamperResult,
    compute_integrity_hmac,
    filter_admissible,
    is_admissible,
    verify_on_open,
)
from depthfusion.cache.hit_rate import (
    HitRateReport,
    HitRateStore,
    generate_report,
)
from depthfusion.cache.lease_lifecycle import (
    CLASSIFICATION_TTL_SECONDS,
    DEFAULT_LEASE_SECONDS,
    CacheWiper,
    InMemoryLeaseStore,
    Lease,
    LeaseManager,
    LeaseStatus,
    LeaseStore,
    OfflineDocument,
    OfflineQueryEngine,
    OfflineResult,
    OfflineResultSet,
    PurgeEngine,
    PurgeResult,
    PurgeTrigger,
    RenewalDeniedError,
    RenewalOutcome,
    RevokedError,
    TokenWiper,
    ttl_for_classification,
)
from depthfusion.cache.manager import CacheManager
from depthfusion.cache.models import CacheEntry, EvictionPolicy
from depthfusion.cache.prefetch_scheduler import (
    PrefetchCandidate,
    PrefetchPlan,
    PrefetchScheduler,
)

__all__ = [
    "CacheManager",
    "CacheEntry",
    "EvictionPolicy",
    # Admission + schema + tamper (E-58 S-188, T-650/T-651)
    "CACHE_SCHEMA",
    "CACHE_SCHEMA_VERSION",
    "AdmissionDecision",
    "CacheableRecord",
    "LeaseRow",
    "TamperResult",
    "compute_integrity_hmac",
    "filter_admissible",
    "is_admissible",
    "verify_on_open",
    # Activity signal store + privacy guard (T-652)
    "_PRIVACY_GUARD",
    "ActivitySignal",
    "ActivitySignalStore",
    "SignalKind",
    # Prefetch scheduler (T-655)
    "PrefetchCandidate",
    "PrefetchPlan",
    "PrefetchScheduler",
    # Hit-rate telemetry (T-656)
    "HitRateReport",
    "HitRateStore",
    "generate_report",
    # Lease lifecycle: issuance/renewal, purge, offline query (S-190)
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
