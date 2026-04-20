"""Backend factory — per-capability backend resolution for DepthFusion v0.5.

Given a capability name and the current mode, returns an `LLMBackend`
implementation. Resolution order:

  1. Per-capability env-var override (e.g. `DEPTHFUSION_RERANKER_BACKEND=null`)
  2. Default dispatch table keyed on `(DEPTHFUSION_MODE, capability)`
  3. Terminal fallback to `NullBackend`

v0.5.0 scaffolding: the `haiku`, `gemma`, and `local` dispatch targets
currently fall back to `NullBackend`. Their actual implementations land
in T-116 (Haiku), T-118 (LocalEmbedding), T-132 (Gemma). The factory's
dispatch table is complete and forward-compatible with those landings.

Fallback-chain ordering (AC-01-8 quality-ranked per DR-018 §4 → I-18):
a future iteration will return a list of backends rather than a single
backend, so callers can cascade on `RateLimitError` / `BackendOverloadError`
/ `BackendTimeoutError`. For v0.5.0 foundation, the factory returns a
single backend and the fallback is implicit (NullBackend when others
haven't shipped yet).

T-123 — Fallback observability:
When a healthy-check fails and the factory falls back from a real backend
(haiku, gemma) to NullBackend, a JSONL event is emitted to the metrics
collector if `DEPTHFUSION_BACKEND_FALLBACK_LOG` is not explicitly disabled.
This gives operators a durable audit trail for silent-degradation events
that would otherwise appear only in Python logging output.

Spec: docs/plans/v0.5/02-build-plan.md §2.2.2
Backlog: T-119, T-123
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from depthfusion.backends.base import LLMBackend
from depthfusion.backends.gemma import GemmaBackend
from depthfusion.backends.haiku import HaikuBackend
from depthfusion.backends.null import NullBackend

logger = logging.getLogger(__name__)

# Per-capability env-var override names. Values override the mode default.
_CAPABILITY_ENV_VARS = {
    "reranker": "DEPTHFUSION_RERANKER_BACKEND",
    "extractor": "DEPTHFUSION_EXTRACTOR_BACKEND",
    "linker": "DEPTHFUSION_LINKER_BACKEND",
    "summariser": "DEPTHFUSION_SUMMARISER_BACKEND",
    "embedding": "DEPTHFUSION_EMBEDDING_BACKEND",
    "decision_extractor": "DEPTHFUSION_DECISION_EXTRACTOR_BACKEND",
}

# Default dispatch keyed on (mode, capability). Values are backend names.
# Missing entries default to "null".
_DEFAULT_DISPATCH = {
    ("local", "reranker"): "null",
    ("local", "extractor"): "null",
    ("local", "linker"): "null",
    ("local", "summariser"): "null",
    ("local", "embedding"): "null",
    ("local", "decision_extractor"): "null",
    ("vps-cpu", "reranker"): "haiku",
    ("vps-cpu", "extractor"): "haiku",
    ("vps-cpu", "linker"): "haiku",
    ("vps-cpu", "summariser"): "haiku",
    ("vps-cpu", "embedding"): "null",  # opt-in via env-var override
    ("vps-cpu", "decision_extractor"): "haiku",
    ("vps-gpu", "reranker"): "gemma",
    ("vps-gpu", "extractor"): "gemma",
    ("vps-gpu", "linker"): "gemma",
    ("vps-gpu", "summariser"): "gemma",
    ("vps-gpu", "embedding"): "local",
    ("vps-gpu", "decision_extractor"): "gemma",
}

# v0.5 compatibility alias: the legacy single "vps" mode maps to vps-cpu.
# Removed in v0.6 (see 02-build-plan.md §2.3.1).
_VPS_LEGACY_ALIAS = "vps-cpu"


def get_backend(capability: str, *, mode: Optional[str] = None) -> LLMBackend:
    """Return the configured `LLMBackend` for a capability.

    Args:
        capability: one of `reranker`, `extractor`, `linker`, `summariser`,
            `embedding`, `decision_extractor`.
        mode: `DEPTHFUSION_MODE` override. Defaults to the env var
            `DEPTHFUSION_MODE` (default `"local"` when unset).

    Raises:
        ValueError: on unknown capability or unknown backend name.

    Returns:
        An `LLMBackend` instance. Always a concrete object — never `None`.
    """
    if capability not in _CAPABILITY_ENV_VARS:
        known = sorted(_CAPABILITY_ENV_VARS)
        raise ValueError(
            f"Unknown capability: {capability!r}. Known capabilities: {known}"
        )

    env_var = _CAPABILITY_ENV_VARS[capability]
    override = os.environ.get(env_var, "").strip().lower()
    if override:
        return _instantiate(override, capability)

    resolved_mode = (mode or os.environ.get("DEPTHFUSION_MODE") or "local").strip().lower()
    if resolved_mode == "vps":
        resolved_mode = _VPS_LEGACY_ALIAS  # v0.5 alias; deprecated

    default_name = _DEFAULT_DISPATCH.get((resolved_mode, capability), "null")
    return _instantiate(default_name, capability)


def _instantiate(name: str, capability: str) -> LLMBackend:
    """Construct the backend by name. v0.5 scaffolding: haiku/gemma/local
    fall through to NullBackend until their implementations land.
    """
    if name == "null":
        return NullBackend()

    if name == "haiku":
        # T-116 HaikuBackend. If construction succeeds but the backend
        # reports unhealthy (no DEPTHFUSION_API_KEY, no SDK), fall through
        # to NullBackend rather than returning an unusable backend.
        haiku: LLMBackend = HaikuBackend()
        if haiku.healthy():
            return haiku
        logger.info(
            "HaikuBackend requested for capability %r but not healthy "
            "(no DEPTHFUSION_API_KEY or anthropic SDK); falling back to NullBackend.",
            capability,
        )
        _emit_fallback_event(
            requested=name,
            capability=capability,
            reason="unhealthy: no DEPTHFUSION_API_KEY or anthropic SDK unavailable",
        )
        return NullBackend()

    if name == "gemma":
        # T-132 GemmaBackend. Healthy whenever URL + model are configured
        # (construction-time check; no network probe). Falls back to
        # NullBackend only in the extreme case of empty config.
        gemma: LLMBackend = GemmaBackend()
        if gemma.healthy():
            return gemma
        logger.info(
            "GemmaBackend requested for capability %r but not healthy "
            "(missing URL or model config); falling back to NullBackend.",
            capability,
        )
        _emit_fallback_event(
            requested=name,
            capability=capability,
            reason="unhealthy: DEPTHFUSION_GEMMA_URL or DEPTHFUSION_GEMMA_MODEL empty",
        )
        return NullBackend()

    if name == "local":
        # T-118 will implement LocalEmbeddingBackend. Scaffold: fall through.
        return NullBackend()

    known = {"null", "haiku", "gemma", "local"}
    raise ValueError(
        f"Unknown backend: {name!r} for capability {capability!r}. "
        f"Known backends: {sorted(known)}"
    )


def _emit_fallback_event(
    requested: str,
    capability: str,
    reason: str,
) -> None:
    """Emit a JSONL fallback-event record to the metrics collector.

    Gated on DEPTHFUSION_BACKEND_FALLBACK_LOG (default: enabled).
    Errors are swallowed — observability must never degrade serving.

    T-123 contract:
      metric name : "backend.fallback"
      value       : 1.0  (increment; aggregator sums over windows)
      labels      : requested, capability, reason
    """
    raw = os.environ.get("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return

    try:
        from depthfusion.metrics.collector import MetricsCollector  # lazy import
        MetricsCollector().record(
            "backend.fallback",
            1.0,
            labels={
                "requested": requested,
                "actual": "null",
                "capability": capability,
                "reason": reason,
            },
        )
    except Exception as exc:  # noqa: BLE001
        # Observability must never break serving. Log at DEBUG only.
        logger.debug("_emit_fallback_event: could not write metrics record: %s", exc)


__all__ = ["get_backend"]
