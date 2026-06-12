"""depthfusion MCP tool implementations — recall domain."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

from depthfusion.mcp.tools._shared import (
    _detect_current_backends,
    _tool_recall_impl,
)
from depthfusion.mcp.tools._state import _get_hnsw_store

logger = logging.getLogger("depthfusion.mcp.server")


def _tool_recall(arguments: dict) -> str:
    """Retrieve relevant context blocks across three sources using BM25 + RRF.

    v0.5.2 S-60 / T-186: thin wrapper around `_tool_recall_impl` that
    measures total latency, counts returned blocks, and emits a
    `record_recall_query` JSONL event on every call.
    v0.5.2 S-61 / T-193: threads a mutable `perf_ms` dict through the
    impl so per-capability phase latencies ride out to the metrics
    record. Phases that didn't run are absent from the dict (not
    zero) — absence is the signal for "this capability wasn't invoked".
    Metrics emission failures are swallowed so observability can never
    break recall.
    """
    import hashlib

    t0 = time.monotonic()
    event_subtype = "ok"
    response_json = ""
    perf_ms: dict[str, float] = {}
    try:
        response_json = _tool_recall_impl(arguments, perf_ms=perf_ms)
    except Exception as exc:
        event_subtype = "error"
        response_json = json.dumps(
            {
                "error": str(exc),
                "query": str(arguments.get("query", "")),
                "blocks": [],
                "strategy": "bm25-only",
                "hnsw_available": _get_hnsw_store() is not None,
            }
        )

    # Best-effort metrics emission — never raises into the caller.
    try:
        result_count = 0
        chunk_ids: list[str] = []
        try:
            parsed = json.loads(response_json) if response_json else {}
            blocks_parsed = parsed.get("blocks", []) or []
            result_count = len(blocks_parsed)
            chunk_ids = [
                b["chunk_id"] for b in blocks_parsed if isinstance(b.get("chunk_id"), str)
            ]
        except (json.JSONDecodeError, TypeError):
            pass

        from depthfusion.metrics.collector import MetricsCollector
        query = str(arguments.get("query", ""))
        query_hash = (
            hashlib.sha256(query.encode("utf-8")).hexdigest()[:12] if query else ""
        )
        # Backend-routing snapshot — the factory is the authoritative
        # source. We record the resolved name per capability at emit time
        # so each query reflects the CURRENT routing, not a stale cache.
        # Skip the 6× probe on the error path (the path is already
        # degraded; adding probe overhead doesn't add observability value).
        # S-80 / T-268: pass perf_ms so probe latencies seed all six
        # capability keys; pipeline-level measurements (reranker, embedding)
        # already in perf_ms take precedence — they were recorded before
        # this point, so they are NOT overwritten here.  Probe-time entries
        # are written to a copy so we can selectively fill only capabilities
        # that don't already have a pipeline measurement.
        # S-83 / T-278: populate the per-query `backend_fallback_chain`
        # alongside `backend_used`. Single-backend resolutions record
        # `[name]`; FallbackChain resolutions record the cascade
        # (split from backend.name on "+"). Empty on the error path —
        # the legacy `backend.fallback*` simple-stream events remain the
        # complementary aggregate-count source there.
        backend_fallback_chain: dict[str, list[str]] = {}
        if event_subtype == "ok":
            _probe_ms: dict[str, float] = {}
            backend_used = _detect_current_backends(
                perf_ms=_probe_ms,
                fallback_chain=backend_fallback_chain,
            )
            # Merge: pipeline measurements win; probe times fill any gap.
            for _cap, _t in _probe_ms.items():
                if _cap not in perf_ms:
                    perf_ms[_cap] = _t
        else:
            backend_used = {}
        total_latency_ms = (time.monotonic() - t0) * 1000.0

        MetricsCollector().record_recall_query(
            query_hash=query_hash,
            mode=os.environ.get("DEPTHFUSION_MODE", "local"),
            backend_used=backend_used,
            backend_fallback_chain=backend_fallback_chain,
            latency_ms_per_capability=perf_ms,
            total_latency_ms=round(total_latency_ms, 3),
            result_count=result_count,
            event_subtype=event_subtype,
            chunk_ids=chunk_ids,
        )
    except Exception as exc:  # noqa: BLE001 — observability must not raise
        logger.debug("recall metrics emission failed: %s", exc)

    return response_json

def _tool_recall_feedback(arguments: dict) -> str:
    """E-27 / S-72 — recall feedback loop entry point."""
    recall_id = arguments.get("recall_id")
    used = arguments.get("used", [])
    ignored = arguments.get("ignored", [])

    if not isinstance(recall_id, str) or not recall_id.strip():
        return json.dumps({
            "ok": False,
            "error": "recall_feedback: 'recall_id' must be a non-empty string",
        })
    if not isinstance(used, list) or not isinstance(ignored, list):
        return json.dumps({
            "ok": False,
            "error": "recall_feedback: 'used' and 'ignored' must be lists",
        })
    if not all(isinstance(c, str) for c in used + ignored):
        return json.dumps({
            "ok": False,
            "error": "recall_feedback: chunk_ids must be strings",
        })

    from depthfusion.core.feedback import RecallStore
    result = RecallStore.singleton().apply_feedback(
        recall_id, used=list(used), ignored=list(ignored),
    )
    return json.dumps(result.to_dict())

def _tool_retrieve_context(arguments: dict, config: Any) -> str:
    from depthfusion.cognitive.scorer import CognitiveScorer, ScoringContext
    from depthfusion.retrieval.hybrid import fts_prefilter_memory_ids
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    query = arguments.get("query", "")
    top_k = int(arguments.get("top_k", 10))
    memory_types = arguments.get("memory_types")

    store = MemoryStore(config.memory_store_path)

    # S-114: use FTS5 prefilter when available to reduce the BM25/scoring
    # candidate set. Falls through to full-table query when FTS is off or
    # the query is empty.
    fts_ids = fts_prefilter_memory_ids(store, query) if query else None
    if fts_ids is not None:
        # FTS returned a ranked candidate list; load only those IDs
        memories = [m for mid in fts_ids if (m := store.get(mid)) is not None]
    else:
        memories = store.query(
            project_id=project_id or None,
            memory_type=memory_types[0] if memory_types and len(memory_types) == 1 else None,
            limit=top_k * 4,
        )

    scorer = CognitiveScorer()
    scored = []
    for m in memories:
        ctx = ScoringContext(confidence=m.confidence.score)
        score = scorer.score(ctx)
        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    def _memory_block(score: float, m: "Any") -> dict:
        block: dict = {
            "memory_id": m.id,
            "type": m.type.value,
            "content": m.content[:500],
            "score": score,
        }
        # S-112 AC-2: include structured fields when present
        facts = m.extra.get("facts") or []
        concepts = m.extra.get("concepts") or []
        files_read = m.extra.get("files_read") or []
        files_modified = m.extra.get("files_modified") or []
        if facts:
            block["facts"] = facts
        if concepts:
            block["concepts"] = concepts
        if files_read:
            block["files_read"] = files_read
        if files_modified:
            block["files_modified"] = files_modified
        return block

    return json.dumps({
        "query": query,
        "project_id": project_id,
        "memories": [_memory_block(s, m) for s, m in top],
        "count": len(top),
    })

def register_recall() -> None:
    """Register recall domain tools (stub for v2 tooling framework)."""
    pass
