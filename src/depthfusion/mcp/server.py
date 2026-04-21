"""DepthFusion MCP server — 5 tools, conditionally registered based on feature flags."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any

from depthfusion.retrieval.bm25 import BM25 as _BM25
from depthfusion.retrieval.bm25 import tokenize as _tokenize_bm25

logger = logging.getLogger(__name__)

TOOLS: dict[str, str] = {
    "depthfusion_status": "Return current DepthFusion component status",
    "depthfusion_recall_relevant": (
        "Retrieve most relevant session blocks for a query. "
        "Args: query (str), top_k (int, default 5), snippet_len (int, default 1500), "
        "cross_project (bool, default False — when True, searches all projects), "
        "project (str, optional — override auto-detected project slug). "
        "Response: {query, blocks: [...], message, total_sources_scanned}. "
        "On internal error, response may also include `error: str` with the "
        "exception message; `blocks` is always present (empty list on error)."
    ),
    "depthfusion_tag_session": "Tag a session file with metadata",
    "depthfusion_publish_context": "Publish a context item to the bus",
    "depthfusion_run_recursive": "Run recursive LLM on large content",
    # v0.3.0 additions
    "depthfusion_tier_status": "Return corpus size, active tier, and promotion estimate",
    "depthfusion_auto_learn": "Trigger auto-learning extraction from recent session files",
    "depthfusion_compress_session": "Compress a specific .tmp session file into a discovery file",
    # v0.4.0 graph tools
    "depthfusion_graph_traverse": "Traverse entity graph from a named entity",
    "depthfusion_graph_status": "Report graph health: node count, edge count, coverage, tier",
    "depthfusion_set_scope": "Set session graph scope (project | cross_project | global)",
    # v0.5.0 CM-5 active confirmation tool
    "depthfusion_confirm_discovery": "Actively confirm a decision or fact for immediate capture",
    # v0.5.1 TG-14 / S-55 discovery pruner
    "depthfusion_prune_discoveries": (
        "Identify stale discovery files in ~/.claude/shared/discoveries/. "
        "Args: age_days (int, default 90 or DEPTHFUSION_PRUNE_AGE_DAYS), "
        "confirm (bool, default False). Without confirm=True, returns "
        "candidates with reasons but does NOT move any files. "
        "With confirm=True, moves to ~/.claude/shared/discoveries/.archive/ "
        "(never deletes — reversible)."
    ),
}

# Map tools to the feature flags that gate them
_TOOL_FLAGS: dict[str, str | None] = {
    "depthfusion_status": None,               # always enabled
    "depthfusion_recall_relevant": None,       # always enabled
    "depthfusion_tag_session": None,           # always enabled
    "depthfusion_publish_context": "router_enabled",
    "depthfusion_run_recursive": "rlm_enabled",
    "depthfusion_tier_status": None,
    "depthfusion_auto_learn": None,
    "depthfusion_compress_session": None,
    "depthfusion_graph_traverse": "graph_enabled",
    "depthfusion_graph_status": "graph_enabled",
    "depthfusion_set_scope": "graph_enabled",
    "depthfusion_confirm_discovery": None,          # always enabled (CM-5)
    "depthfusion_prune_discoveries": None,          # always enabled (TG-14 / S-55)
}


def get_enabled_tools(config: Any) -> list[str]:
    """Return list of tool names enabled by current config.

    Tools gated by a feature flag are excluded if that flag is False.
    Tools with no flag are always included.
    """
    enabled: list[str] = []
    for tool_name, flag_attr in _TOOL_FLAGS.items():
        if flag_attr is None:
            enabled.append(tool_name)
        elif getattr(config, flag_attr, False):
            enabled.append(tool_name)
    return enabled


def _make_tool_schema(name: str, description: str) -> dict:
    """Build a minimal MCP tool schema."""
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }


def _handle_tools_list(config: Any) -> dict:
    enabled = get_enabled_tools(config)
    return {
        "tools": [_make_tool_schema(n, TOOLS[n]) for n in enabled]
    }


def _handle_tools_call(tool_name: str, arguments: dict, config: Any) -> dict:
    """Dispatch a tool call and return MCP-formatted result."""
    if tool_name not in TOOLS:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
        }

    enabled = get_enabled_tools(config)
    if tool_name not in enabled:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Tool {tool_name} is disabled by config"}],
        }

    # Dispatch to tool implementations
    try:
        result_text = _dispatch_tool(tool_name, arguments, config)
        return {
            "isError": False,
            "content": [{"type": "text", "text": result_text}],
        }
    except Exception as exc:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Tool error: {exc}"}],
        }


def _dispatch_tool(tool_name: str, arguments: dict, config: Any) -> str:
    """Route tool calls to their implementations."""
    if tool_name == "depthfusion_status":
        return _tool_status(config)
    elif tool_name == "depthfusion_recall_relevant":
        return _tool_recall(arguments)
    elif tool_name == "depthfusion_tag_session":
        return _tool_tag_session(arguments)
    elif tool_name == "depthfusion_publish_context":
        return _tool_publish_context(arguments)
    elif tool_name == "depthfusion_run_recursive":
        return _tool_run_recursive(arguments, config)
    elif tool_name == "depthfusion_tier_status":
        return _tool_tier_status()
    elif tool_name == "depthfusion_auto_learn":
        return _tool_auto_learn(arguments)
    elif tool_name == "depthfusion_compress_session":
        return _tool_compress_session(arguments)
    elif tool_name == "depthfusion_graph_traverse":
        return _tool_graph_traverse(arguments)
    elif tool_name == "depthfusion_graph_status":
        return _tool_graph_status()
    elif tool_name == "depthfusion_set_scope":
        return _tool_set_scope(arguments)
    elif tool_name == "depthfusion_confirm_discovery":
        return _tool_confirm_discovery(arguments)
    elif tool_name == "depthfusion_prune_discoveries":
        return _tool_prune_discoveries(arguments)
    else:
        raise ValueError(f"No dispatcher for {tool_name}")


def _tool_status(config: Any) -> str:
    enabled = get_enabled_tools(config)
    return json.dumps(
        {
            "depthfusion": "active",
            "enabled_tools": enabled,
            "rlm_enabled": getattr(config, "rlm_enabled", True),
            "router_enabled": getattr(config, "router_enabled", True),
            "session_enabled": getattr(config, "session_enabled", True),
            "fusion_enabled": getattr(config, "fusion_enabled", True),
        },
        indent=2,
    )


## ---------------------------------------------------------------------------
## Block extraction: chunk files on H2 headers for finer-grained retrieval
## (BM25 is imported at module-top to avoid ruff E402; see L11-12.)
## ---------------------------------------------------------------------------

def _split_into_blocks(content: str, source_label: str, file_stem: str) -> list[dict]:
    """Split file content into blocks on '\\n## ' headers.

    Each block gets a unique chunk_id and inherits the file's source label.
    Files with no H2 headers are returned as a single block.
    """
    # Split on H2 markdown headers (## at line start)
    sections = re.split(r"\n(?=## )", content)
    blocks = []
    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue
        # Extract a title from the first line if it starts with ##
        first_line = section.split("\n", 1)[0]
        title = first_line.lstrip("#").strip() if first_line.startswith("#") else ""
        chunk_id = f"{file_stem}#{i}" if len(sections) > 1 else file_stem
        blocks.append({
            "chunk_id": chunk_id,
            "file_stem": file_stem,
            "source": source_label,
            "content": section,
            "title": title,
        })
    return blocks if blocks else [{"chunk_id": file_stem, "file_stem": file_stem,
                                   "source": source_label, "content": content, "title": ""}]


## ---------------------------------------------------------------------------
## Source weights: memory (user-written) > discovery > session (machine-generated)
## ---------------------------------------------------------------------------

_SOURCE_WEIGHTS = {
    "memory": 1.0,
    "discovery": 0.85,
    "session": 0.70,
}


def _trim_to_sentence(text: str, max_len: int) -> str:
    """Trim *text* to at most *max_len* characters, preferring a sentence boundary.

    Rules (applied in order):
    1. If ``len(text) <= max_len`` return text unchanged.
    2. Truncate to ``max_len`` characters.
    3. Search backwards for the last sentence-ending character (``.``, ``!``,
       ``?``, or ``\\n``) in the truncated slice.
    4. If found **and** the break point is at least 60 % of ``max_len``
       characters from the start (to avoid returning an overly-short result),
       trim there (inclusive of the sentence-ending character).
    5. Otherwise, trim at the last space (word boundary).
    6. Append ``…`` to indicate truncation.
    """
    if len(text) <= max_len:
        return text

    truncated = text[:max_len]

    # Step 3 – look for last sentence boundary
    min_pos = int(max_len * 0.6)
    last_sentence = -1
    for char in (".", "!", "?", "\n"):
        pos = truncated.rfind(char)
        if pos >= min_pos and pos > last_sentence:
            last_sentence = pos

    if last_sentence != -1:
        return truncated[: last_sentence + 1] + "…"

    # Step 5 – fall back to last word boundary
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space] + "…"

    # No boundary found – hard cut
    return truncated + "…"


# S-52 / T-161: slug sanitisation for externally-supplied `project` args.
# MCP clients can pass `project="..."` to _tool_recall and _tool_confirm_discovery.
# Without sanitisation, a malicious slug like "../other" could traverse outside
# ~/.claude/shared/discoveries/ when used as a filename component (as
# write_decisions does). Same allowlist as git_post_commit.detect_project().
_SLUG_ALLOW_RE = re.compile(r"[^a-z0-9-]")


def _sanitise_project_slug(slug: str) -> str:
    """Lowercase, allow only [a-z0-9-], collapse other chars to '-', cap at 40.

    Returns empty string for inputs that sanitise to nothing (pure separators,
    empty, whitespace-only) so callers can treat it as "no project provided".
    """
    if not slug:
        return ""
    cleaned = _SLUG_ALLOW_RE.sub("-", slug.strip().lower())[:40].strip("-")
    return cleaned


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
    import time

    t0 = time.monotonic()
    event_subtype = "ok"
    response_json = ""
    perf_ms: dict[str, float] = {}
    try:
        response_json = _tool_recall_impl(arguments, perf_ms=perf_ms)
    except Exception as exc:
        event_subtype = "error"
        response_json = json.dumps(
            {"error": str(exc), "query": str(arguments.get("query", "")), "blocks": []}
        )

    # Best-effort metrics emission — never raises into the caller.
    try:
        result_count = 0
        try:
            parsed = json.loads(response_json) if response_json else {}
            result_count = len(parsed.get("blocks", []) or [])
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
        backend_used = _detect_current_backends() if event_subtype == "ok" else {}
        total_latency_ms = (time.monotonic() - t0) * 1000.0

        MetricsCollector().record_recall_query(
            query_hash=query_hash,
            mode=os.environ.get("DEPTHFUSION_MODE", "local"),
            backend_used=backend_used,
            latency_ms_per_capability=perf_ms,
            total_latency_ms=round(total_latency_ms, 3),
            result_count=result_count,
            event_subtype=event_subtype,
        )
    except Exception as exc:  # noqa: BLE001 — observability must not raise
        logger.debug("recall metrics emission failed: %s", exc)

    return response_json


def _detect_current_backends() -> dict[str, str]:
    """Return {capability: backend_name} for all 6 LLM capabilities.

    Resolves via `get_backend(...)` so the routing reflects the live env
    (including any DEPTHFUSION_*_BACKEND overrides). Fails-closed to an
    empty dict on any error — the record still emits, just without
    routing detail for the failed probe.
    """
    result: dict[str, str] = {}
    try:
        from depthfusion.backends.factory import get_backend
        for cap in ("reranker", "extractor", "linker", "summariser",
                    "embedding", "decision_extractor"):
            try:
                result[cap] = get_backend(cap).name
            except Exception:  # noqa: BLE001 — per-cap failure → skip
                continue
    except Exception:  # noqa: BLE001
        pass
    return result


def _tool_recall_impl(arguments: dict, *, perf_ms: dict | None = None) -> str:
    """Core recall logic — extracted from `_tool_recall` for wrapping with
    metrics emission (S-60 / T-186). Preserves the full v0.5.1 behaviour.

    Sources:
    1. ~/.claude/sessions/*.tmp  — goal session state files (cross-session memory)
    2. ~/.claude/shared/discoveries/*.md — discovery files written by /goal and agents
    3. ~/.claude/projects/-home-gregmorris/memory/*.md — persistent memory files

    v0.5.2 S-61: the caller may pass a mutable `perf_ms: dict[str, float]`
    that this function populates with per-capability phase latencies.
    Only phases that actually run write entries — absence means the
    phase didn't execute for this query. Current phases tracked:
      * `reranker` — `pipeline.apply_reranker` wall-clock time (in ms)
      * `fusion_gates` — `pipeline.apply_fusion_gates` wall-clock time
        (only when `DEPTHFUSION_FUSION_GATES_ENABLED=true`)
    """
    import time
    from pathlib import Path

    if perf_ms is None:
        perf_ms = {}  # local scratch if caller didn't provide one

    query = arguments.get("query", "")
    top_k = int(arguments.get("top_k", 5))
    snippet_len = int(arguments.get("snippet_len", 1500))
    # T-161 / S-52: project scoping. When cross_project=False (the default),
    # results are filtered to the current project (auto-detected via git
    # remote or DEPTHFUSION_PROJECT env var). cross_project=True restores
    # the v0.4.x behaviour of returning discoveries from every project.
    cross_project = bool(arguments.get("cross_project", False))
    # Optional explicit project override — useful for tests and for MCP
    # clients that know their project context better than git does.
    # Sanitise against path-traversal: a malicious client could pass
    # `project="../../etc"`, which _tool_confirm_discovery would otherwise
    # propagate to write_decisions() as a filename component.
    _raw_explicit = str(arguments.get("project", "")).strip()
    explicit_project = _sanitise_project_slug(_raw_explicit) or None

    home = Path.home()
    raw_blocks: list[dict] = []  # list of {chunk_id, file_stem, source, content, title}

    # T-160: parse `project:` frontmatter once per file and attach it to each
    # block we derive from that file. This survives the ## section split in
    # _split_into_blocks — the frontmatter lives only in block 0 otherwise.
    from depthfusion.retrieval.hybrid import extract_frontmatter_project

    def _load_file(md_file: "Path", source_label: str) -> None:
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if not content.strip():
            return
        file_project = extract_frontmatter_project(content)
        for block in _split_into_blocks(content, source_label, md_file.stem):
            if file_project is not None:
                block["project"] = file_project
            raw_blocks.append(block)

    # Source 1: goal session state files (.tmp)
    sessions_dir = home / ".claude" / "sessions"
    if sessions_dir.exists():
        for tmp_file in sorted(sessions_dir.glob("*.tmp"),
                               key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            _load_file(tmp_file, "session")

    # Source 2: shared discoveries
    discoveries_dir = home / ".claude" / "shared" / "discoveries"
    if discoveries_dir.exists():
        for md_file in sorted(discoveries_dir.glob("*.md"),
                              key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            if md_file.name == "README.md":
                continue
            _load_file(md_file, "discovery")

    # Source 3: persistent memory files
    memory_dir = home / ".claude" / "projects" / "-home-gregmorris" / "memory"
    if memory_dir.exists():
        for md_file in sorted(memory_dir.glob("*.md"),
                              key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            if md_file.name == "MEMORY.md":
                continue
            _load_file(md_file, "memory")

    if not raw_blocks:
        return json.dumps({"query": query, "blocks": [], "message": "No session context available"})

    # S-52 T-161: apply project-scoped filter before scoring so BM25 IDF
    # weights are computed against the filtered corpus, not the full
    # cross-project corpus.
    if not cross_project:
        current_project = explicit_project
        if current_project is None:
            try:
                from depthfusion.hooks.git_post_commit import detect_project
                detected = detect_project()
            except Exception:
                detected = ""
            # detect_project() never returns None or empty — it falls back
            # to the sanitised cwd-directory name, or the literal "unknown"
            # when that also fails. Treat "unknown" as "no project context"
            # rather than filtering against a literal slug that no real
            # discovery file would ever have — otherwise recall in a bare
            # MCP client with no git remote would silently return zero blocks.
            if detected and detected != "unknown":
                current_project = detected
            else:
                current_project = None
        if current_project:
            from depthfusion.retrieval.hybrid import filter_blocks_by_project
            before_count = len(raw_blocks)
            raw_blocks = filter_blocks_by_project(
                raw_blocks, current_project=current_project, cross_project=False,
            )
            if not raw_blocks:
                return json.dumps({
                    "query": query, "blocks": [],
                    "message": (
                        f"No context found for project {current_project!r} "
                        f"(filtered {before_count} blocks). Pass "
                        "cross_project=true to search all projects."
                    ),
                })

    # Recency ordering: insertion order reflects mtime desc (used as a small tie-breaker)
    recency_list: list[str] = [b["chunk_id"] for b in raw_blocks]

    if not query.strip():
        # No query: return recency-ordered blocks with no scoring
        top = raw_blocks[:top_k]
        blocks_out = []
        for b in top:
            snippet = _trim_to_sentence(b["content"].strip(), snippet_len)
            blocks_out.append({
                "chunk_id": b["chunk_id"],
                "source": b["source"],
                "score": 0.5,
                "snippet": snippet,
            })
        return json.dumps({
            "query": query,
            "blocks": blocks_out,
            "total_sources_scanned": len(raw_blocks),
            "message": f"Retrieved {len(blocks_out)} blocks (recency order, no query)",
        }, indent=2)

    # BM25 scoring with source-type weights
    corpus_tokens = [_tokenize_bm25(b["content"]) for b in raw_blocks]
    query_tokens = _tokenize_bm25(query)
    bm25 = _BM25(corpus_tokens)
    bm25_ranked = bm25.rank_all(query_tokens)  # list of (idx, raw_bm25_score)

    # Apply source-type weight to BM25 scores
    weighted: list[tuple[int, float]] = []
    for idx, raw_score in bm25_ranked:
        source = raw_blocks[idx]["source"]
        weight = _SOURCE_WEIGHTS.get(source, 1.0)
        # recency_rank gives a small tie-breaking boost (0–1% of score) without
        # overriding content signal
        chunk_id = raw_blocks[idx]["chunk_id"]
        recency_rank = (
            recency_list.index(chunk_id) if chunk_id in recency_list
            else len(recency_list)
        )
        recency_boost = 1.0 / (1 + recency_rank * 0.01)  # max 1%, fades quickly
        weighted.append((idx, raw_score * weight * recency_boost))

    weighted.sort(key=lambda x: -x[1])

    # Build reranker input: deduplicate by file_stem, keep highest-scoring chunk per file
    reranker_input = []
    seen_files: set[str] = set()
    for idx, final_score in weighted:
        if final_score <= 0.0:
            break
        b = raw_blocks[idx]
        if b["file_stem"] in seen_files:
            continue
        seen_files.add(b["file_stem"])
        snippet = _trim_to_sentence(b["content"].strip(), snippet_len)
        reranker_input.append({
            "chunk_id": b["chunk_id"],
            "file_stem": b["file_stem"],
            "source": b["source"],
            "score": round(final_score, 4),
            "snippet": snippet,
        })

    # VPS Tier 1+2: apply pipeline (reranker / ChromaDB fusion)
    from depthfusion.retrieval.hybrid import RecallPipeline
    pipeline = RecallPipeline.from_env()

    # S-62 / T-196: apply vector search BEFORE fusion gates and reranking.
    # `apply_vector_search` calls `get_backend("embedding")` — on vps-gpu
    # this is `LocalEmbeddingBackend` (sentence-transformers); on other
    # modes it's `NullBackend` which returns None → the method returns
    # [] → `rrf_fuse` degrades gracefully to BM25-only. Gated on
    # DEPTHFUSION_VECTOR_SEARCH_ENABLED so v0.5.x byte-identity is
    # preserved when the flag is off (default).
    if (
        os.environ.get("DEPTHFUSION_VECTOR_SEARCH_ENABLED", "false").lower()
        in ("true", "1", "yes")
        and reranker_input
    ):
        _t_vec = time.monotonic()
        vector_results = pipeline.apply_vector_search(
            query, reranker_input, top_k=max(top_k * 2, 10),
        )
        if vector_results:
            # RRF-fuse BM25 (reranker_input, already ranked) with the
            # vector-search ordering. Output is the fused list — replace
            # the reranker input so downstream phases see the fused pool.
            reranker_input = pipeline.rrf_fuse(reranker_input, vector_results)
        perf_ms["vector_search"] = round((time.monotonic() - _t_vec) * 1000.0, 3)

    # S-61: apply fusion gates BEFORE reranking when enabled. The
    # gates (Mamba B/C/Δ) filter the candidate pool by query similarity
    # + topical coherence + α-blended threshold; the reranker then
    # orders what the gates admitted. Phase is timed only when gates
    # actually run (env flag on + non-empty input); the `perf_ms` dict
    # gets a `fusion_gates` entry only in that case.
    if (
        os.environ.get("DEPTHFUSION_FUSION_GATES_ENABLED", "false").lower()
        in ("true", "1", "yes")
        and reranker_input
    ):
        _t_gates = time.monotonic()
        reranker_input = pipeline.apply_fusion_gates(reranker_input, query=query)
        perf_ms["fusion_gates"] = round((time.monotonic() - _t_gates) * 1000.0, 3)

    # Apply reranker (no-op in local mode, haiku in vps-tier1+2).
    # Time this phase only in non-LOCAL modes where the reranker actually
    # calls an LLM backend — in LOCAL mode `apply_reranker` is a list slice.
    _t_rerank = time.monotonic()
    blocks_out = pipeline.apply_reranker(reranker_input, query, top_k=top_k)
    if pipeline.mode.value != "local":
        perf_ms["reranker"] = round((time.monotonic() - _t_rerank) * 1000.0, 3)
    # Ensure output blocks have consistent fields
    for b in blocks_out:
        if "snippet" not in b:
            b["snippet"] = _trim_to_sentence(b.get("content", "").strip(), snippet_len)
        b.pop("file_stem", None)
        b.pop("content", None)

    return json.dumps({
        "query": query,
        "blocks": blocks_out,
        "total_sources_scanned": len(raw_blocks),
        "message": f"Retrieved {len(blocks_out)} relevant blocks (BM25+RRF)",
    }, indent=2)


def _tool_tag_session(arguments: dict) -> str:
    session_id = arguments.get("session_id", "")
    tags = arguments.get("tags", [])
    return json.dumps({"session_id": session_id, "tags": tags, "tagged": True})


def _tool_publish_context(arguments: dict) -> str:
    item = arguments.get("item", {})
    return json.dumps({"published": True, "item": item})


def _tool_run_recursive(arguments: dict, config: Any) -> str:
    query = arguments.get("query", "")
    content = arguments.get("content", "")
    try:
        from depthfusion.recursive.client import RLMClient
        client = RLMClient(config=config)
        if not client.is_available():
            return json.dumps({"error": "rlm package not available", "result": None})
        result_text, traj = client.run(query=query, content=content)
        return json.dumps(
            {
                "result": result_text,
                "strategy": traj.strategy,
                "tokens": traj.total_tokens,
                "cost": traj.estimated_cost,
            }
        )
    except Exception as exc:
        return json.dumps({"error": str(exc), "result": None})


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


def _tool_auto_learn(arguments: dict) -> str:
    """Trigger auto-learn extraction from recent .tmp session files."""
    from pathlib import Path
    max_files = min(int(arguments.get("max_files", 5)), 50)
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.exists():
        return json.dumps({"compressed": 0, "message": "No sessions directory"})
    try:
        from depthfusion.capture.compressor import SessionCompressor
        compressor = SessionCompressor()
        recent = sorted(sessions_dir.glob("*.tmp"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
        results = []
        for tmp in recent:
            out = compressor.compress(tmp)
            if out:
                results.append(str(out.name))
        return json.dumps({
            "compressed": len(results),
            "files": results,
            "message": f"Auto-learned from {len(results)} session files",
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "compressed": 0})


def _tool_compress_session(arguments: dict) -> str:
    """Compress a specific .tmp file into a discovery file."""
    from pathlib import Path
    session_path_str = arguments.get("session_path", "")
    if not session_path_str:
        return json.dumps({"error": "session_path argument required"})
    try:
        from depthfusion.capture.compressor import SessionCompressor
        compressor = SessionCompressor()
        out = compressor.compress(Path(session_path_str))
        if out:
            return json.dumps({"success": True, "output": str(out)})
        return json.dumps({
            "success": False,
            "message": "Nothing to compress (empty or already done)",
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_graph_traverse(arguments: dict) -> str:
    """Traverse entity graph from a named entity."""
    import os
    graph_enabled = os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true"
    if not graph_enabled:
        return json.dumps({"error": "DEPTHFUSION_GRAPH_ENABLED is not set"})

    from depthfusion.graph.store import get_store
    from depthfusion.graph.traverser import traverse

    entity_name = arguments.get("entity_name", "")
    depth = min(int(arguments.get("depth", 1)), 3)
    relationship_filter = arguments.get("relationship_filter") or None

    store = get_store()
    all_entities = store.all_entities()
    match = next(
        (e for e in all_entities if e.name.lower() == entity_name.lower()), None
    )
    if not match:
        return json.dumps({
            "error": f"Entity not found: {entity_name}",
            "available": [e.name for e in all_entities[:20]],
        })

    result = traverse(match.entity_id, store, depth=depth, relationship_filter=relationship_filter)
    if not result:
        return json.dumps({"error": "Traversal failed"})

    return json.dumps({
        "origin": {
            "name": result.origin_entity.name,
            "type": result.origin_entity.type,
            "confidence": result.origin_entity.confidence,
        },
        "connected": [
            {
                "name": e.name, "type": e.type, "relationship": edge.relationship,
                "weight": edge.weight, "signals": edge.signals,
            }
            for e, edge in result.connected
        ],
        "depth": result.depth,
    }, indent=2)


def _tool_graph_status() -> str:
    """Report graph health and coverage."""
    import os
    graph_enabled = os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true"
    if not graph_enabled:
        return json.dumps({
            "graph_enabled": False,
            "message": "Set DEPTHFUSION_GRAPH_ENABLED=true to activate",
        })

    from depthfusion.graph.store import get_store
    store = get_store()
    entities = store.all_entities()
    type_breakdown: dict[str, int] = {}
    for e in entities:
        type_breakdown[e.type] = type_breakdown.get(e.type, 0) + 1

    return json.dumps({
        "graph_enabled": True,
        "node_count": store.node_count(),
        "edge_count": store.edge_count(),
        "entities_by_type": type_breakdown,
        "tier": os.environ.get("DEPTHFUSION_MODE", "local"),
    }, indent=2)


def _tool_confirm_discovery(arguments: dict) -> str:
    """CM-5: Actively confirm a decision or fact for immediate capture.

    Writes a discovery file tagged `type: decisions` immediately — no LLM call
    required. Claude can call this during a session to capture an architectural
    decision, confirmed value, or established pattern the moment it is resolved.

    Arguments:
        text     (str, required): The decision or fact to capture (≤ 300 chars)
        project  (str, optional): Project slug (auto-detected from cwd if absent)
        category (str, optional): one of decision|fact|pattern|error_fix|value
                                   (default: "decision")
        confidence (float, optional): 0.0–1.0 (default: 0.95 — user confirmed)
    """
    text = str(arguments.get("text", "")).strip()
    if not text:
        return json.dumps({
            "ok": False,
            "error": "text argument is required",
        })
    if len(text) > 300:
        text = text[:300]

    # Sanitise any externally-supplied slug against path traversal before it
    # reaches write_decisions() (which uses the slug as a filename component).
    project = _sanitise_project_slug(str(arguments.get("project", "")))
    if not project:
        # Auto-detect from git remote or cwd; guard the import so a broken
        # git_post_commit module can't take down the confirmation tool.
        try:
            from depthfusion.hooks.git_post_commit import detect_project
            project = detect_project()
        except Exception:
            project = "unknown"

    category = str(arguments.get("category", "decision")).strip()
    if category not in ("decision", "fact", "pattern", "error_fix", "value"):
        category = "decision"

    confidence = float(arguments.get("confidence", 0.95))
    confidence = max(0.0, min(1.0, confidence))


    from depthfusion.capture.decision_extractor import DecisionEntry, write_decisions

    entry = DecisionEntry(
        text=text,
        confidence=confidence,
        category=category,
        source_session="mcp_confirm",
    )

    try:
        # S-60 / T-190: the metrics bucket for this path is
        # "confirm_discovery" (the high-level MCP tool), not
        # "decision_extractor" (the underlying writer). The override
        # kwarg on write_decisions threads the mechanism name through.
        out = write_decisions(
            [entry],
            project=project,
            session_id="mcp_confirm",
            capture_mechanism="confirm_discovery",
        )
        if out:
            return json.dumps({
                "ok": True,
                "written": str(out),
                "project": project,
                "text": text,
                "category": category,
                "confidence": confidence,
            }, indent=2)
        # File already exists for today — still succeeds, just idempotent
        return json.dumps({
            "ok": True,
            "written": None,
            "note": "Discovery file for today already exists; entry not appended "
                    "(use a new session or delete the file to re-capture)",
            "project": project,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


def _tool_prune_discoveries(arguments: dict) -> str:
    """TG-14 / S-55: identify and optionally archive stale discovery files.

    Two-phase design:
      1. `confirm=False` (default) — return candidate list with reasons.
         No filesystem modification. Operator reviews the list.
      2. `confirm=True` — move listed candidates to
         `~/.claude/shared/discoveries/.archive/`. Never deletes.

    Arguments:
        age_days (int, optional): override the default 90-day threshold
            (or `DEPTHFUSION_PRUNE_AGE_DAYS` env var).
        confirm (bool, optional): when True, actually move the files.

    Returns:
        JSON with `candidates` (list of {path, reason, age_days}) and
        `moved` (list of archive paths, empty when confirm=False).
        On error, returns `{"ok": False, "error": "..."}`.
    """
    try:
        age_days_raw = arguments.get("age_days")
        age_days: int | None
        if age_days_raw is None:
            age_days = None
        else:
            age_days = int(age_days_raw)
            if age_days <= 0:
                return json.dumps({
                    "ok": False,
                    "error": f"age_days must be positive, got {age_days}",
                })
        confirm = bool(arguments.get("confirm", False))
    except (TypeError, ValueError) as exc:
        return json.dumps({"ok": False, "error": f"invalid arguments: {exc}"})

    try:
        from depthfusion.capture.pruner import (
            identify_candidates,
            prune_discoveries,
        )
        candidates = identify_candidates(age_days=age_days)
        candidates_json = [
            {
                "path": str(c.path),
                "reason": c.reason,
                "age_days": c.age_days,
            }
            for c in candidates
        ]

        if not confirm:
            return json.dumps({
                "ok": True,
                "candidates": candidates_json,
                "moved": [],
                "message": (
                    f"{len(candidates)} prune candidates identified. "
                    "Pass confirm=true to move them to "
                    "~/.claude/shared/discoveries/.archive/"
                ),
            }, indent=2)

        moved = prune_discoveries(candidates, confirm=True)
        return json.dumps({
            "ok": True,
            "candidates": candidates_json,
            "moved": [str(p) for p in moved],
            "message": f"Moved {len(moved)} file(s) to archive.",
        }, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


def _tool_set_scope(arguments: dict) -> str:
    """Programmatically set session graph scope."""
    from datetime import datetime, timezone

    from depthfusion.graph.scope import write_scope
    from depthfusion.graph.types import GraphScope

    mode = arguments.get("mode", "project")
    projects = arguments.get("projects") or []

    if mode not in ("project", "cross_project", "global"):
        return json.dumps({"error": f"Invalid mode: {mode}. Use project|cross_project|global"})

    scope = GraphScope(
        mode=mode,
        active_projects=projects,
        session_id="mcp_set",
        set_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    write_scope(scope)
    return json.dumps({"ok": True, "mode": mode, "active_projects": projects})


def _process_request(request: dict, config: Any) -> dict:
    """Process a single JSON-RPC request and return the response."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "depthfusion", "version": "0.4.0"},
        }
    elif method == "tools/list":
        result = _handle_tools_list(config)
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = _handle_tools_call(tool_name, arguments, config)
    elif method == "notifications/initialized":
        # Notification — no response needed
        return {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def main() -> None:
    """MCP server entry point.

    Reads config from env, registers enabled tools, serves over stdio (JSON-RPC).
    """
    from depthfusion.core.config import DepthFusionConfig

    config = DepthFusionConfig.from_env()
    enabled = get_enabled_tools(config)
    logger.info(f"DepthFusion MCP server starting — {len(enabled)} tools enabled")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = _process_request(request, config)
            if response:
                print(json.dumps(response), flush=True)
        except json.JSONDecodeError as exc:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            print(json.dumps(error_response), flush=True)
        except Exception as exc:
            logger.error(f"Unhandled error: {exc}")


if __name__ == "__main__":
    main()
