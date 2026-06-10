"""depthfusion MCP tool implementations — shared recall helpers."""
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

## ---------------------------------------------------------------------------
## Block extraction: chunk files on H2 headers for finer-grained retrieval
## ---------------------------------------------------------------------------

## ---------------------------------------------------------------------------
## Source weights: memory (user-written) > discovery > session (machine-generated)
## ---------------------------------------------------------------------------

_SOURCE_WEIGHTS = {
    "memory": 1.0,
    "rule": 0.95,       # user-defined conventions and standards — high authority
    "discovery": 0.85,
    "session": 0.70,
}

# S-52 / T-161: slug sanitisation for externally-supplied `project` args.
_SLUG_ALLOW_RE = re.compile(r"[^a-z0-9-]")


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

def _sanitise_project_slug(slug: str) -> str:
    """Lowercase, allow only [a-z0-9-], collapse other chars to '-', cap at 40.

    Returns empty string for inputs that sanitise to nothing (pure separators,
    empty, whitespace-only) so callers can treat it as "no project provided".
    """
    if not slug:
        return ""
    cleaned = _SLUG_ALLOW_RE.sub("-", slug.strip().lower())[:40].strip("-")
    return cleaned

def _backend_name_to_chain(name: str) -> list[str]:
    """Split a (possibly composite) backend name into its cascade list.

    `FallbackChain.name` is the literal `"+".join(b.name for b in chain)`,
    e.g. ``"gemma+haiku+null"``. A single-backend resolution has no ``+``
    in its name, so the result is ``[name]``. A composite name splits to
    the underlying cascade order. Empty / falsy names return ``[]``.

    S-83 / T-278: this is the building block for the per-query
    ``backend_fallback_chain`` field in recall events. Every capability
    is recorded as ``[name]`` (single backend) or ``[name1, name2, ...]``
    (cascade) — never empty for a successfully-resolved capability — so
    the aggregator's ``per_capability_fallback`` view always has a value
    to walk for capabilities that appeared in ``backend_used``.
    """
    if not name:
        return []
    return [part for part in name.split("+") if part]

def _detect_current_backends(
    perf_ms: "dict[str, float] | None" = None,
    fallback_chain: "dict[str, list[str]] | None" = None,
) -> dict[str, str]:
    """Return {capability: backend_name} for all 6 LLM capabilities.

    Resolves via `get_backend(...)` so the routing reflects the live env
    (including any DEPTHFUSION_*_BACKEND overrides). Fails-closed to an
    empty dict on any error — the record still emits, just without
    routing detail for the failed probe.

    S-80 / T-268: when `perf_ms` is supplied, each backend probe is timed
    and the wall-clock duration (ms) is written into `perf_ms[cap]`.
    This seeds latency entries for all six capabilities; capabilities that
    the recall pipeline actually invokes (``reranker``, ``embedding``)
    will have their probe-time entry overwritten by the more precise
    in-pipeline measurement recorded in ``_tool_recall_impl``.
    Capabilities not invoked during recall (``extractor``, ``linker``,
    ``summariser``, ``decision_extractor``) retain the probe-time latency
    — it is the only real backend interaction for those capabilities within
    the scope of a recall event.

    S-83 / T-278: when `fallback_chain` is supplied, each resolved
    capability writes its cascade list (split from ``backend.name`` on
    ``+``) into ``fallback_chain[cap]``. Single-backend resolutions
    record ``[name]``; ``FallbackChain`` resolutions record the full
    cascade in declared order (e.g. ``["gemma", "haiku", "null"]``).
    This drives the structured ``backend_fallback_chain`` field in the
    recall stream — complementary to the legacy aggregate-count
    ``backend.fallback`` / ``backend.runtime_fallback`` simple-stream
    events emitted from ``factory.py`` and ``chain.py`` respectively.
    """
    import time as _time  # shadow-free local import for timing

    result: dict[str, str] = {}
    try:
        from depthfusion.backends.factory import get_backend
        for cap in ("reranker", "extractor", "linker", "summariser",
                    "embedding", "decision_extractor"):
            try:
                _t = _time.monotonic()
                backend = get_backend(cap)
                result[cap] = backend.name
                if perf_ms is not None:
                    perf_ms[cap] = round((_time.monotonic() - _t) * 1000.0, 3)
                if fallback_chain is not None:
                    fallback_chain[cap] = _backend_name_to_chain(backend.name)
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
    explain = bool(arguments.get("explain", False))
    # S-113: 3-layer retrieval depth. "full" is the current default behaviour.
    mode = str(arguments.get("mode", "full"))
    if mode not in ("full", "index", "timeline"):
        mode = "full"
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

    recall_id: str | None = None  # minted after raw_blocks assembled; None on empty-result paths

    home = Path.home()
    raw_blocks: list[dict] = []  # list of {chunk_id, file_stem, source, content, title}

    # T-160: parse `project:` frontmatter once per file and attach it to each
    # block we derive from that file. This survives the ## section split in
    # _split_into_blocks — the frontmatter lives only in block 0 otherwise.
    from depthfusion.retrieval.hybrid import (
        boilerplate_penalty as _boilerplate_penalty,
    )
    from depthfusion.retrieval.hybrid import (
        extract_frontmatter_project,
        extract_frontmatter_sub_scope,
    )
    from depthfusion.retrieval.hybrid import (
        extract_session_project as _extract_session_project,
    )
    from depthfusion.retrieval.hybrid import (
        lexical_richness_penalty as _lexical_richness_penalty,
    )
    from depthfusion.retrieval.hybrid import (
        query_hits_boost as _query_hits_boost,
    )

    def _load_file(md_file: "Path", source_label: str) -> None:
        from datetime import datetime
        from datetime import timezone as _tz
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if not content.strip():
            return
        try:
            mtime_iso: str | None = datetime.fromtimestamp(
                md_file.stat().st_mtime, tz=_tz.utc
            ).isoformat()
        except OSError:
            mtime_iso = None
        file_project = extract_frontmatter_project(content)
        # For session files that lack YAML frontmatter, parse the project slug
        # from the plain-text session event header ("Project: <slug>").
        # This corrects the back-compat hole that let all session blocks
        # through the project filter regardless of which project they belong to.
        if file_project is None and source_label == "session":
            file_project = _extract_session_project(content)
        file_sub_scope = extract_frontmatter_sub_scope(content)
        for block in _split_into_blocks(content, source_label, md_file.stem):
            if mtime_iso is not None:
                block["mtime_iso"] = mtime_iso
            if file_project is not None:
                block["project"] = file_project
            if file_sub_scope is not None:
                block["sub_scope"] = file_sub_scope
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

    # Source 4: global user rules (~/.claude/rules/*.md) and project-local rules
    # (.claude/rules/*.md in the current working directory). Rules files encode
    # conventions, standards, and workflow preferences — high-signal for queries
    # about coding style, commit format, error handling, test strategy, etc.
    for rules_dir in [
        home / ".claude" / "rules",
        Path.cwd() / ".claude" / "rules",
    ]:
        if rules_dir.exists() and rules_dir.is_dir():
            for md_file in sorted(rules_dir.glob("*.md"),
                                  key=lambda p: p.stat().st_mtime, reverse=True)[:25]:
                if md_file.name.startswith("_") or md_file.name == "README.md":
                    continue
                _load_file(md_file, "rule")

    # E-45: surface HNSW availability on every recall response (even early exits).
    _hnsw_store_handle = _get_hnsw_store()
    _hnsw_available = _hnsw_store_handle is not None

    if not raw_blocks:
        return json.dumps({
            "query": query, "blocks": [], "recall_id": None,
            "message": "No session context available",
            "strategy": "bm25-only",
            "hnsw_available": _hnsw_available,
        })

    # S-52 T-161: apply project-scoped filter before scoring so BM25 IDF
    # weights are computed against the filtered corpus, not the full
    # cross-project corpus.
    # S-92: initialise current_project=None here so it's always in scope when
    # explain data is assembled (the variable is only populated inside the
    # cross_project branch below, but the explain loop runs outside of it).
    current_project: str | None = None
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
            from depthfusion.retrieval.hybrid import (
                detect_mentioned_projects as _dmp,
            )
            from depthfusion.retrieval.hybrid import (
                filter_blocks_by_project,
                filter_blocks_by_sub_scope,
            )
            # Detect projects explicitly named in the query so their blocks are
            # included even when cross_project=False.  Example: a query like
            # "I'm working on the SkillForge router" from a depthfusion session
            # should still surface skillforge context.
            _all_tagged = {
                b["project"] for b in raw_blocks if isinstance(b.get("project"), str)
            }
            _mentioned = _dmp(query, _all_tagged) - {current_project}
            before_count = len(raw_blocks)
            raw_blocks = filter_blocks_by_project(
                raw_blocks,
                current_project=current_project,
                cross_project=False,
                extra_projects=frozenset(_mentioned) if _mentioned else None,
            )
            # ADR-001 / OD-3: Room filter — applied to Wing survivors only.
            # sub_scope=None (no active Room) is a no-op (back-compat).
            from depthfusion.graph.scope import read_scope as _read_scope_for_recall
            _active_scope = _read_scope_for_recall()
            _sub_scope = _active_scope.sub_scope if _active_scope is not None else None
            raw_blocks = filter_blocks_by_sub_scope(raw_blocks, sub_scope=_sub_scope)
            if not raw_blocks:
                return json.dumps({
                    "query": query, "blocks": [],
                    "recall_id": None,
                    "message": (
                        f"No context found for project {current_project!r} "
                        f"(filtered {before_count} blocks). Pass "
                        "cross_project=true to search all projects."
                    ),
                    "strategy": "bm25-only",
                    "hnsw_available": _hnsw_available,
                })

    # S-72: mint recall_id after filtering so chunk_ids match the caller-visible set.
    from depthfusion.core.feedback import RecallStore
    from depthfusion.core.hit_tracker import HitTracker
    recall_id = RecallStore.singleton().register_recall(
        [b["chunk_id"] for b in raw_blocks]
    )

    # S-113: lightweight index/timeline modes bypass BM25 entirely — O(n) scan.
    if mode in ("index", "timeline"):
        from depthfusion.retrieval.hybrid import index_pass, timeline_pass
        if mode == "index":
            pass_blocks = index_pass(raw_blocks, top_k=top_k)
            msg = f"Retrieved {len(pass_blocks)} index entries (no scoring)"
        else:
            pass_blocks = timeline_pass(raw_blocks, top_k=top_k)
            msg = f"Retrieved {len(pass_blocks)} entries (recency order, no scoring)"
        return json.dumps({
            "query": query,
            "mode": mode,
            "count": len(pass_blocks),
            "blocks": pass_blocks,
            "recall_id": recall_id,
            "total_sources_scanned": len(raw_blocks),
            "message": msg,
            "strategy": "bm25-only",
            "hnsw_available": _hnsw_available,
        }, indent=2)

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
            "recall_id": recall_id,
            "total_sources_scanned": len(raw_blocks),
            "message": f"Retrieved {len(blocks_out)} blocks (recency order, no query)",
            "strategy": "bm25-only",
            "hnsw_available": _hnsw_available,
        }, indent=2)

    # BM25 scoring with source-type weights
    corpus_tokens = [_tokenize_bm25(b["content"]) for b in raw_blocks]
    query_tokens = _tokenize_bm25(query)
    bm25 = _BM25(corpus_tokens)
    # S-112: field boost — tokenize per-block facts+concepts; empty for
    # legacy markdown blocks (no boost), non-empty for ContextItem-derived
    # blocks whose query terms match a structured field (1.2× lift).
    _field_tokens: list[list[str]] = [
        [
            tok
            for entry in ((b.get("facts") or []) + (b.get("concepts") or []))
            for tok in _tokenize_bm25(str(entry))
        ]
        for b in raw_blocks
    ]
    bm25_ranked = bm25.rank_with_field_boost(query_tokens, _field_tokens)

    # Apply source-type weight to BM25 scores
    # S-92: per-block explain data (only populated when explain=True)
    _query_lower = query.lower()
    _explain_data: dict[int, dict] = {}
    weighted: list[tuple[int, float]] = []
    _tracker = HitTracker.singleton()
    for idx, raw_score in bm25_ranked:
        _block = raw_blocks[idx]
        source = _block["source"]
        weight = _SOURCE_WEIGHTS.get(source, 1.0)
        # recency_rank gives a small tie-breaking boost (0–1% of score) without
        # overriding content signal
        chunk_id = _block["chunk_id"]
        recency_rank = (
            recency_list.index(chunk_id) if chunk_id in recency_list
            else len(recency_list)
        )
        recency_boost = 1.0 / (1 + recency_rank * 0.01)  # max 1%, fades quickly
        # Boilerplate penalty: session blocks that are pure lifecycle envelopes
        # (SESSION START/END + JSON metadata, ≤12 non-empty lines) score 0.2×.
        bp = _boilerplate_penalty(_block.get("content", ""))
        # Project mention boost: when the query names the block's project slug,
        # lift that block 2× so cross-project results the user explicitly asked
        # about outrank boilerplate from the current project.
        _blk_proj = _block.get("project", "")
        mention_boost = (
            2.0
            if _blk_proj and len(_blk_proj) >= 4 and _blk_proj.lower() in _query_lower
            else 1.0
        )
        lr = _lexical_richness_penalty(_block.get("content", ""))
        qh = _query_hits_boost(_block.get("chunk_id", ""), _tracker)
        final_score = raw_score * weight * recency_boost * bp * mention_boost * lr * qh
        weighted.append((idx, final_score))
        if explain:
            _proj_match: bool | None = (
                (_block.get("project") == current_project)
                if (not cross_project and current_project is not None)
                else None
            )
            _explain_data[idx] = {
                "bm25_score": round(raw_score, 4),
                "source_weight": weight,
                "boilerplate_penalty": round(bp, 2),
                "mention_boost": round(mention_boost, 2),
                "lexical_richness": round(lr, 4),
                "query_hits_boost": round(qh, 4),
                "project_match": _proj_match,
            }

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
        entry: dict = {
            "chunk_id": b["chunk_id"],
            "file_stem": b["file_stem"],
            "source": b["source"],
            "score": round(final_score, 4),
            "snippet": snippet,
        }
        # S-92: stash BM25-phase explain data internally so it survives into
        # the post-reranker loop. This field is stripped before output.
        if explain and idx in _explain_data:
            entry["_explain"] = _explain_data[idx]
        reranker_input.append(entry)

    # VPS Tier 1+2: apply pipeline (reranker / ChromaDB fusion)
    from depthfusion.retrieval.hybrid import _BLEND_MODE, RecallPipeline
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
        try:
            vector_results = pipeline.apply_vector_search(
                query, reranker_input, top_k=max(top_k * 2, 10),
            )
            if vector_results:
                # Fuse BM25 (reranker_input, already ranked) with the
                # vector-search ordering. S-121: DEPTHFUSION_BLEND_MODE=linear
                # activates MemPalace-style min-max blend; default is RRF.
                if _BLEND_MODE == "linear":
                    reranker_input = pipeline.linear_blend(reranker_input, vector_results)
                else:
                    reranker_input = pipeline.rrf_fuse(reranker_input, vector_results)
        finally:
            # S-80 AC-3: record latency even when apply_vector_search raises.
            # Also record under the canonical capability key ("embedding") so
            # latency_ms_per_capability always uses backend capability names,
            # not pipeline-phase names.
            _vec_elapsed = round((time.monotonic() - _t_vec) * 1000.0, 3)
            perf_ms["vector_search"] = _vec_elapsed
            perf_ms["embedding"] = _vec_elapsed

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
    # S-80 AC-3: record latency even when the reranker backend returns an
    # error — wrap in try/finally so the elapsed time is captured before
    # the exception propagates to the outer try/except in `_tool_recall`.
    _t_rerank = time.monotonic()
    try:
        blocks_out = pipeline.apply_reranker(reranker_input, query, top_k=top_k)
    finally:
        if pipeline.mode.value != "local":
            perf_ms["reranker"] = round((time.monotonic() - _t_rerank) * 1000.0, 3)
    # Ensure output blocks have consistent fields; attach explain block when requested.
    for rank_idx, b in enumerate(blocks_out):
        if "snippet" not in b:
            b["snippet"] = _trim_to_sentence(b.get("content", "").strip(), snippet_len)
        b.pop("file_stem", None)
        b.pop("content", None)
        # S-92: build the public explain block from internal _explain plus pipeline scores.
        # Security constraint (AC-4): only numeric scores, booleans, and rank integer —
        # no env values, no extended path components, no cross-project names.
        if explain:
            ex: dict = {}
            if "_explain" in b:
                ex.update(b["_explain"])
            ex["rrf_score"] = b.get("score")
            if "vector_score" in b:
                ex["vector_score"] = b["vector_score"]
            ex["reranker_rank"] = rank_idx
            b["explain"] = {k: v for k, v in ex.items() if v is not None}
        b.pop("_explain", None)  # always strip internal field

    # S-76: build engaged_layers from what actually ran this call
    engaged_layers = ["bm25"]
    if "vector_search" in perf_ms:
        engaged_layers.append("embedding")
    if "fusion_gates" in perf_ms:
        engaged_layers.append("fusion_gates")
    if "reranker" in perf_ms:
        engaged_layers.append("reranker")
    if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true":
        engaged_layers.append("graph_traverse")

    # E-45: HNSW post-hoc fusion (BM25 + dense vector). Behind feature flag;
    # NEVER lets HNSW failure crash the BM25 path.
    strategy = "bm25-only"
    if _hnsw_store_handle is not None:
        try:
            hnsw_hits = _hnsw_store_handle.search(query, k=max(top_k * 2, 10))
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.debug("[hnsw] search raised during fusion: %s", exc)
            hnsw_hits = []
        if hnsw_hits:
            engaged_layers.append("hnsw")
            # Map both raw discovery_id and a file_stem-prefix view so we can
            # cross-reference BM25 chunk_ids (which look like "file_stem#N" or
            # plain "file_stem").
            hnsw_by_did: dict[str, float] = {
                hit["discovery_id"]: float(hit.get("score", 0.0)) for hit in hnsw_hits
            }
            # Apply fusion boost to existing BM25 blocks.
            for block in blocks_out:
                chunk_id = str(block.get("chunk_id", ""))
                stem = chunk_id.split("#", 1)[0] if "#" in chunk_id else chunk_id
                hnsw_score = hnsw_by_did.get(chunk_id, hnsw_by_did.get(stem))
                bm25_score = float(block.get("score", 0.0))
                if hnsw_score is not None:
                    block["score"] = round(0.6 * bm25_score + 0.4 * hnsw_score, 6)
                    block["source_layer"] = "fused"
                else:
                    block["score"] = round(0.6 * bm25_score, 6)
                    block["source_layer"] = "bm25"

            # Add HNSW-only hits that weren't already in BM25 results.
            existing_ids = {str(b.get("chunk_id", "")) for b in blocks_out}
            existing_stems = {
                cid.split("#", 1)[0] if "#" in cid else cid for cid in existing_ids
            }
            for hit in hnsw_hits:
                did = hit["discovery_id"]
                if did in existing_ids or did in existing_stems:
                    continue
                hnsw_score = float(hit.get("score", 0.0))
                blocks_out.append({
                    "chunk_id": did,
                    "source": "hnsw",
                    "source_layer": "hnsw",
                    "score": round(0.4 * hnsw_score, 6),
                    "snippet": "",
                })

            blocks_out.sort(key=lambda b: -float(b.get("score", 0.0)))
            blocks_out = blocks_out[:top_k]
            strategy = "fused"

    # S-117: record which chunks were returned so future queries can boost them.
    HitTracker.singleton().register_hits(
        [b["chunk_id"] for b in blocks_out], query
    )

    return json.dumps({
        "query": query,
        "blocks": blocks_out,
        "recall_id": recall_id,
        "total_sources_scanned": len(raw_blocks),
        "engaged_layers": engaged_layers,
        "message": f"Retrieved {len(blocks_out)} relevant blocks (BM25+RRF)",
        "strategy": strategy,
        "hnsw_available": _hnsw_available,
    }, indent=2)

