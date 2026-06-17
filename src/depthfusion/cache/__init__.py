"""Intelligent Offline Cache — ML-driven, encrypted at rest.

E-58: LRU cache backed by SQLite with Fernet encryption.
"""

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
from depthfusion.cache.manager import CacheManager
from depthfusion.cache.models import CacheEntry, EvictionPolicy

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
]
