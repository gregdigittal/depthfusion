"""depthfusion MCP tool implementations — telemetry domain."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from depthfusion.capture.event_hook import emit_if_high_importance
from depthfusion.core.types import ContextItem
from depthfusion.parsers import parse_conversation
from depthfusion.retrieval.bm25 import BM25 as _BM25
from depthfusion.retrieval.bm25 import tokenize as _tokenize_bm25
from depthfusion.router.bus import ContextBus, FileBus, InMemoryBus
try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

logger = logging.getLogger("depthfusion.mcp.server")
from depthfusion.mcp.tools._state import _get_hnsw_store, _get_context_bus, _get_fabric_store


def _tool_hnsw_capability() -> str:
    """Return current HNSW index capability/state (E-45)."""
    store = _get_hnsw_store()
    if store is None:
        return json.dumps(
            {
                "enabled": False,
                "backend": "none",
                "model": "",
                "dimension": 0,
                "index_path": "",
                "entry_count": 0,
            }
        )
    try:
        return json.dumps(store.capability())
    except Exception as exc:  # noqa: BLE001 — never crash the tool
        logger.warning("[hnsw] capability() raised: %s", exc)
        return json.dumps(
            {
                "enabled": False,
                "backend": "none",
                "model": "",
                "dimension": 0,
                "index_path": "",
                "entry_count": 0,
            }
        )

def _tool_tier_status() -> str:
    try:
        from depthfusion.storage.tier_manager import TierManager
        tm = TierManager()
        cfg = tm.detect_tier()
        return json.dumps({
            "tier": cfg.tier.value,
            "corpus_size": cfg.corpus_size,
            "threshold": cfg.threshold,
            "sessions_until_promotion": cfg.sessions_until_promotion,
            "mode": cfg.mode,
            "auto_promote": tm.auto_promote,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

def _tool_describe_capabilities() -> str:
    """S-76: describe which layers and mechanisms are engaged on this instance."""
    graph_enabled = os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true"
    haiku_enabled = os.environ.get("DEPTHFUSION_HAIKU_ENABLED", "false").lower() == "true"
    vector_search_enabled = os.environ.get(
        "DEPTHFUSION_VECTOR_SEARCH_ENABLED", "false"
    ).lower() == "true"
    embedding_backend = os.environ.get("DEPTHFUSION_EMBEDDING_BACKEND", "")
    fusion_gates_enabled = os.environ.get(
        "DEPTHFUSION_FUSION_GATES_ENABLED", "false"
    ).lower() == "true"
    router_enabled = os.environ.get("DEPTHFUSION_ROUTER_ENABLED", "true").lower() == "true"
    decision_extractor_enabled = os.environ.get(
        "DEPTHFUSION_DECISION_EXTRACTOR_ENABLED", "false"
    ).lower() == "true"
    install_mode = os.environ.get("DEPTHFUSION_MODE", "local")

    # Determine effective tier
    tier = install_mode
    if install_mode == "vps":
        try:
            from depthfusion.storage.tier_manager import TierManager
            cfg = TierManager().detect_tier()
            tier = cfg.tier.value if hasattr(cfg.tier, "value") else str(cfg.tier)
        except Exception:
            tier = "vps-tier1"

    # Recall layers that will engage on this instance
    recall_layers = ["bm25"]
    if vector_search_enabled and embedding_backend:
        recall_layers.append("embedding")
    if fusion_gates_enabled:
        recall_layers.append("fusion_gates")
    if install_mode == "vps" and haiku_enabled:
        recall_layers.append("reranker")
    if graph_enabled:
        recall_layers.append("graph_traverse")

    # auto_learn capture mechanisms
    auto_learn_layers = ["heuristic"]
    if haiku_enabled:
        auto_learn_layers.append("haiku_summarizer")
    if decision_extractor_enabled:
        auto_learn_layers.append("decision_extractor")
    if graph_enabled and haiku_enabled:
        auto_learn_layers.append("graph_extraction")

    return json.dumps({
        "tier": tier,
        "mode": install_mode,
        "flags": {
            "graph_enabled": graph_enabled,
            "haiku_enabled": haiku_enabled,
            "vector_search_enabled": vector_search_enabled,
            "embedding_backend": embedding_backend or "none",
            "fusion_gates_enabled": fusion_gates_enabled,
            "router_enabled": router_enabled,
            "decision_extractor_enabled": decision_extractor_enabled,
        },
        "engaged_layers_per_op": {
            "recall": recall_layers,
            "publish": ["file_bus" if router_enabled else "disabled"],
            "auto_learn": auto_learn_layers,
        },
        "supported_features": {
            "publish_context": ["structured_fields"],
        },
    }, indent=2)

def _emit_startup_event(tools_enabled: int, metrics_dir: "Path | None" = None) -> None:
    """Write a system.startup record to the legacy metrics stream.

    Serves two purposes: (a) confirms the metrics directory is writable at
    startup rather than discovering the problem during the first real event,
    and (b) makes an empty metrics directory at end-of-day detectable —
    absence of any system.startup record means the MCP server never ran that
    day, which is a distinct condition from "ran but emitted nothing".

    Logs a warning (never raises) so a broken metrics path cannot prevent
    the server from serving tools.  `metrics_dir` is injectable for tests.
    """
    try:
        import importlib.metadata as _meta

        from depthfusion.metrics.collector import MetricsCollector
        try:
            _version = _meta.version("depthfusion")
        except _meta.PackageNotFoundError:
            _version = "unknown"
        MetricsCollector(metrics_dir).record(
            "system.startup",
            1.0,
            {"tools_enabled": tools_enabled, "server_version": _version},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "system.startup event could not be written to metrics directory "
            "(observability degraded — check ~/.claude/depthfusion-metrics/ "
            "permissions): %s",
            exc,
        )

def _check_backend_health(mode: str) -> None:
    """Warn loudly when vps-cpu/vps-gpu mode has backends falling back to NullBackend.

    Users on vps-cpu/vps-gpu expect Haiku/Gemma reranking. If those backends are
    unhealthy (missing API key, SDK not installed, Gemma URL unconfigured), the
    factory silently returns NullBackend and all LLM capabilities degrade to
    no-ops without any visible error. This function surfaces that failure at
    startup so the user sees it in MCP server stderr output.

    Never raises — observability must not block server startup.
    """
    if mode == "local":
        return

    try:
        from depthfusion.backends.factory import get_backend
        from depthfusion.backends.null import NullBackend

        _CAPABILITIES = ("reranker", "extractor", "linker", "summariser", "decision_extractor")
        degraded = []
        for cap in _CAPABILITIES:
            backend = get_backend(cap, mode=mode)
            # A FallbackChain whose first member is NullBackend, or a bare
            # NullBackend, indicates full degradation for this capability.
            if isinstance(backend, NullBackend):
                degraded.append(cap)

        if degraded:
            caps_str = ", ".join(degraded)
            if mode == "vps-cpu":
                diagnosis = (
                    "DEPTHFUSION_API_KEY is unset or the 'anthropic' SDK is not installed. "
                    "Run: pip install 'depthfusion[vps-cpu]'  and set DEPTHFUSION_API_KEY."
                )
            else:  # vps-gpu
                diagnosis = (
                    "DEPTHFUSION_GEMMA_URL or DEPTHFUSION_GEMMA_MODEL is unset, "
                    "or the Gemma sidecar is not running."
                )
            logger.warning(
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║  DepthFusion SILENT DEGRADATION DETECTED                     ║\n"
                "╠══════════════════════════════════════════════════════════════╣\n"
                "║  Mode   : %s                                                 \n"
                "║  Affected capabilities: %s\n"
                "║                                                              ║\n"
                "║  These capabilities are falling back to NullBackend          ║\n"
                "║  (no-op). LLM-assisted reranking, extraction, and linking    ║\n"
                "║  are DISABLED. You are getting BM25-only retrieval.          ║\n"
                "║                                                              ║\n"
                "║  Fix: %s\n"
                "╚══════════════════════════════════════════════════════════════╝",
                mode,
                caps_str,
                diagnosis,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_check_backend_health: could not complete check: %s", exc)

def _tool_record_telemetry(arguments: dict, config: Any) -> str:
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(config.telemetry_store_path)
    event_id = store.record(
        session_id=arguments.get("session_id", ""),
        tool_name=arguments.get("tool_name", ""),
        session_type=arguments.get("session_type", "agent"),
        agent=arguments.get("agent", ""),
        project=arguments.get("project", ""),
        story_id=arguments.get("story_id", ""),
        sprint=arguments.get("sprint", ""),
        duration_ms=arguments.get("duration_ms"),
        tokens_in=arguments.get("tokens_in"),
        tokens_out=arguments.get("tokens_out"),
        cost_usd_estimate=arguments.get("cost_usd_estimate"),
        recorded_at=arguments.get("recorded_at"),
    )
    return json.dumps({"ok": True, "event_id": event_id})

def _tool_query_telemetry(arguments: dict, config: Any) -> str:
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(config.telemetry_store_path)
    result = store.aggregate(
        project=arguments.get("project"),
        agent=arguments.get("agent"),
        session_type=arguments.get("session_type"),
        story_id=arguments.get("story_id"),
        sprint=arguments.get("sprint"),
        period=arguments.get("period"),
        from_dt=arguments.get("from_dt"),
        to_dt=arguments.get("to_dt"),
    )
    return json.dumps(result)

def _tool_surface_skill_candidates(arguments: dict, config: Any) -> str:
    from depthfusion.mcp.skillforge_client import post_skill_draft
    from depthfusion.storage.telemetry_store import TelemetryStore

    _raw = arguments.get("threshold") or getattr(config, "auto_draft_threshold", 3)
    threshold = int(_raw) if _raw is not None else 3
    dry_run: bool = bool(arguments.get("dry_run", False))

    store = TelemetryStore(config.telemetry_store_path)
    patterns = store.get_recurring_patterns(threshold=threshold)

    items = []
    candidates_drafted = 0
    already_tracked = 0

    for pattern in patterns:
        tool_name = pattern["tool_name"]
        session_count = pattern["session_count"]
        pattern_key = f"tool:{tool_name}"
        name = f"Auto-use: {tool_name}"
        description = (
            f"Tool '{tool_name}' used across {session_count} distinct sessions. "
            "Candidate for skill extraction."
        )

        row_id = store.add_candidate(pattern_key, name, description)
        if row_id is None:
            # Already tracked (INSERT OR IGNORE returned 0 rows)
            already_tracked += 1
            items.append(
                {
                    "pattern_key": pattern_key,
                    "name": name,
                    "session_count": session_count,
                    "drafted": False,
                    "skillforge_id": None,
                    "already_tracked": True,
                }
            )
            continue

        skillforge_id: str | None = None
        if not dry_run:
            result = post_skill_draft(
                name=name,
                description=description,
                pattern_key=pattern_key,
                session_count=session_count,
            )
            if result and isinstance(result, dict):
                skillforge_id = str(result.get("id") or result.get("skill_id") or "")
                if skillforge_id:
                    store.update_candidate_skillforge_id(pattern_key, skillforge_id)

        candidates_drafted += 1
        items.append(
            {
                "pattern_key": pattern_key,
                "name": name,
                "session_count": session_count,
                "drafted": True,
                "skillforge_id": skillforge_id,
                "already_tracked": False,
            }
        )

    return json.dumps(
        {
            "candidates_found": len(patterns),
            "candidates_drafted": candidates_drafted,
            "already_tracked": already_tracked,
            "dry_run": dry_run,
            "items": items,
        }
    )

def register_telemetry() -> None:
    """Register telemetry domain tools (stub for v2 tooling framework)."""
    pass
