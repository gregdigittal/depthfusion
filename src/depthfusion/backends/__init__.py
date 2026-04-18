"""DepthFusion LLM backend subpackage (v0.5.0+).

Provides the `LLMBackend` Protocol and a per-capability factory so every
LLM call-site (reranker / extractor / linker / summariser /
decision_extractor / embedding) can be swapped without touching business
logic.

Spec: docs/plans/v0.5/02-build-plan.md §2.2
Epic: E-18 (BACKLOG.md)
"""
from depthfusion.backends.base import (
    BackendExhaustedError,
    BackendOverloadError,
    BackendTimeoutError,
    LLMBackend,
    RateLimitError,
)
from depthfusion.backends.factory import get_backend
from depthfusion.backends.null import NullBackend

__all__ = [
    "LLMBackend",
    "NullBackend",
    "get_backend",
    "RateLimitError",
    "BackendOverloadError",
    "BackendTimeoutError",
    "BackendExhaustedError",
]
