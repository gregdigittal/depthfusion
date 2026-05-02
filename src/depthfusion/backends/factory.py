"""Backend factory — per-capability backend resolution for DepthFusion v0.5.

Given a capability name and the current mode, returns an `LLMBackend`
implementation. Resolution order:

  1. Per-capability env-var override (e.g. `DEPTHFUSION_RERANKER_BACKEND=null`)
  2. Quality-ranked fallback chain keyed on `(DEPTHFUSION_MODE, capability)`
  3. Terminal fallback to `NullBackend`

Quality ranking (descending): gemma (3) > haiku (2) > local (1) > null (0).
The chain is walked in quality-descending order; the first healthy backend
is returned. If there are multiple healthy backends, a `FallbackChain` is
returned so runtime errors (rate-limit, overload, timeout) cascade correctly.

This implements S-41 AC-8 (DR-018 §4 ratification → I-18): cost/latency
optimisation applies only within a quality tier; a lower-quality backend
may never be promoted ahead of a higher-quality one.

T-123 — Fallback observability:
When a healthy-check fails and the factory falls back from a real backend
(haiku, gemma) to NullBackend, a JSONL event is emitted to the metrics
collector if `DEPTHFUSION_BACKEND_FALLBACK_LOG` is not explicitly disabled.

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

# Quality-ranked fallback chains per (mode, capability).
# Lists are quality-descending: highest-quality backend first, NullBackend last.
# DR-018 §4 (I-18): cost/latency may reorder within a tier but not across tiers.
_QUALITY_CHAINS: dict[tuple[str, str], list[str]] = {
    ("local", "reranker"):           ["null"],
    ("local", "extractor"):          ["null"],
    ("local", "linker"):             ["null"],
    ("local", "summariser"):         ["null"],
    ("local", "embedding"):          ["null"],
    ("local", "decision_extractor"): ["null"],
    ("vps-cpu", "reranker"):           ["haiku", "null"],
    ("vps-cpu", "extractor"):          ["haiku", "null"],
    ("vps-cpu", "linker"):             ["haiku", "null"],
    ("vps-cpu", "summariser"):         ["haiku", "null"],
    ("vps-cpu", "embedding"):          ["null"],  # opt-in via env-var override
    ("vps-cpu", "decision_extractor"): ["haiku", "null"],
    ("vps-gpu", "reranker"):           ["gemma", "haiku", "null"],
    ("vps-gpu", "extractor"):          ["gemma", "haiku", "null"],
    ("vps-gpu", "linker"):             ["gemma", "haiku", "null"],
    ("vps-gpu", "summariser"):         ["gemma", "haiku", "null"],
    ("vps-gpu", "embedding"):          ["local", "null"],
    ("vps-gpu", "decision_extractor"): ["gemma", "haiku", "null"],
}

# v0.5 compatibility alias: the legacy single "vps" mode maps to vps-cpu.
# Removed in v0.6 (see 02-build-plan.md §2.3.1).
_VPS_LEGACY_ALIAS = "vps-cpu"


def get_backend(capability: str, *, mode: Optional[str] = None) -> LLMBackend:
    """Return the configured `LLMBackend` for a capability.

    Returns a `FallbackChain` when multiple backends in the quality chain are
    healthy (enabling runtime cascade on `RateLimitError` / `BackendOverloadError`
    / `BackendTimeoutError`). Returns a single backend when only one is healthy.
    Always returns a concrete object — never `None`.

    Args:
        capability: one of `reranker`, `extractor`, `linker`, `summariser`,
            `embedding`, `decision_extractor`.
        mode: `DEPTHFUSION_MODE` override. Defaults to the env var
            `DEPTHFUSION_MODE` (default `"local"` when unset).

    Raises:
        ValueError: on unknown capability or unknown backend name.
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

    chain_names = _QUALITY_CHAINS.get((resolved_mode, capability), ["null"])
    return _resolve_chain(chain_names, capability)


def _resolve_chain(names: list[str], capability: str) -> LLMBackend:
    """Walk a quality-ranked list of backend names and return the best result.

    Rules (DR-018 §4 / I-18 — quality-descending order preserved):
      - If 2+ real (non-null) backends are healthy, return a `FallbackChain`
        so runtime errors (rate-limit, overload) cascade across quality tiers.
      - If exactly 1 real backend is healthy, return it directly.
      - If no real backends are healthy, return `NullBackend`.

    NullBackend is always the terminal fallback and is included in the chain
    only when other real backends are also present (so chain.name includes it).
    """
    from depthfusion.backends.chain import FallbackChain

    real_backends: list[LLMBackend] = []
    for name in names:
        if name == "null":
            continue
        backend = _try_construct(name, capability)
        if backend is not None:
            real_backends.append(backend)

    if not real_backends:
        return NullBackend()
    if len(real_backends) == 1:
        return real_backends[0]
    # Multiple real backends available — wrap in FallbackChain with null terminal.
    return FallbackChain([*real_backends, NullBackend()])


def _try_construct(name: str, capability: str) -> LLMBackend | None:
    """Construct a backend by name. Returns None (with log + fallback event)
    if the backend is requested but not healthy.
    """
    if name == "null":
        return NullBackend()

    if name == "haiku":
        haiku: LLMBackend = HaikuBackend()
        if haiku.healthy():
            return haiku
        logger.info(
            "HaikuBackend requested for capability %r but not healthy "
            "(no DEPTHFUSION_API_KEY or anthropic SDK); skipping in chain.",
            capability,
        )
        _emit_fallback_event(
            requested=name,
            capability=capability,
            reason="unhealthy: no DEPTHFUSION_API_KEY or anthropic SDK unavailable",
        )
        return None

    if name == "gemma":
        gemma: LLMBackend = GemmaBackend()
        if gemma.healthy():
            return gemma
        logger.info(
            "GemmaBackend requested for capability %r but not healthy "
            "(missing URL or model config); skipping in chain.",
            capability,
        )
        _emit_fallback_event(
            requested=name,
            capability=capability,
            reason="unhealthy: DEPTHFUSION_GEMMA_URL or DEPTHFUSION_GEMMA_MODEL empty",
        )
        return None

    if name == "local":
        from depthfusion.backends.local_embedding import LocalEmbeddingBackend
        local: LLMBackend = LocalEmbeddingBackend()
        if local.healthy():
            return local
        logger.info(
            "LocalEmbeddingBackend requested for capability %r but not healthy "
            "(sentence_transformers not installed); skipping in chain.",
            capability,
        )
        _emit_fallback_event(
            requested=name,
            capability=capability,
            reason="unhealthy: sentence_transformers package not importable",
        )
        return None

    known = {"null", "haiku", "gemma", "local"}
    raise ValueError(
        f"Unknown backend: {name!r} for capability {capability!r}. "
        f"Known backends: {sorted(known)}"
    )


def _instantiate(name: str, capability: str) -> LLMBackend:
    """Single-name backend construction (used by env-var override path)."""
    result = _try_construct(name, capability)
    return result if result is not None else NullBackend()


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
