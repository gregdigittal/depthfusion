"""Intelligent Offline Cache — ML-driven, encrypted at rest.

E-58: LRU cache backed by SQLite with Fernet encryption.
"""

from depthfusion.cache.manager import CacheManager
from depthfusion.cache.models import CacheEntry, EvictionPolicy

__all__ = ["CacheManager", "CacheEntry", "EvictionPolicy"]
