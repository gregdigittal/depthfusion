"""DepthFusion MCP server — 5 tools, conditionally registered based on feature flags."""
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
from depthfusion.retrieval.bm25 import BM25 as _BM25
from depthfusion.retrieval.bm25 import tokenize as _tokenize_bm25
from depthfusion.router.bus import ContextBus, FileBus, InMemoryBus

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
    "depthfusion_publish_context": (
        "Publish a context item to the bus with idempotent dedup by content_hash (S-78). "
        "Args: item (object with item_id, content, source_agent, tags; optional priority, "
        "ttl_seconds, metadata). Response: {published: bool, item_id: str, deduped: bool}. "
        "On dedup, `item_id` is the ORIGINAL stored item's id, not the retry's. "
        "Idempotency is exact-content: any byte-level difference in `content` produces "
        "a different hash and is stored as a new item; tag-only or metadata-only "
        "differences do not affect the hash and still dedupe."
    ),
    "depthfusion_run_recursive": "Run recursive LLM on large content",
    # v0.3.0 additions
    "depthfusion_tier_status": "Return corpus size, active tier, and promotion estimate",
    "depthfusion_auto_learn": "Trigger auto-learning extraction from recent session files",
    "depthfusion_compress_session": "Compress a specific .tmp session file into a discovery file",
    # v0.4.0 graph tools
    "depthfusion_graph_traverse": "Traverse entity graph from a named entity",
    "depthfusion_graph_status": "Report graph health: node count, edge count, coverage, tier",
    "depthfusion_set_scope": (
        "Set session graph scope (project | cross_project | global). "
        "Optionally pass sub_scope to further narrow recall to a single "
        "subsystem within the project."
    ),
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
    # E-27 / S-70 explicit operator override of memory scoring
    "depthfusion_set_memory_score": (
        "Override importance and/or salience scalars on a discovery file (S-70). "
        "Args: filename (str, required) — absolute path to a discovery markdown "
        "file; importance (float, optional) ∈ [0.0, 1.0]; salience (float, "
        "optional) ∈ [0.0, 5.0]. At least one of importance/salience must be "
        "supplied. Idempotent — calling with the same values produces the same "
        "file state. Atomic — uses tmp + os.replace so a process kill mid-write "
        "leaves the previous file intact. Out-of-range values are clamped via "
        "MemoryScore. Returns {ok: bool, importance, salience} or "
        "{ok: false, error}."
    ),
    "depthfusion_recall_feedback": (
        "Apply bounded salience deltas based on which retrieved chunks were "
        "actually used (S-72). Args: recall_id (str, required) — uuid4 from a "
        "prior recall_relevant response; used (chunk_id[]) — chunks that were "
        "useful (each contributes +0.1 salience to its discovery file); ignored "
        "(chunk_id[]) — chunks that were not (each contributes -0.05). "
        "Idempotent — replaying the same payload skips already-applied chunks. "
        "Response: {ok, applied, skipped_unsupported, skipped_missing, "
        "skipped_already_applied, skipped_expired}."
    ),
    # S-69 pin — exempt high-value discoveries from age-based pruning
    "depthfusion_pin_discovery": (
        "Pin or unpin a discovery file so it is exempt from age-based pruning "
        "(S-69). Args: filename (str, required) — absolute path to a discovery "
        "markdown file; pinned (bool, optional, default true) — true to pin, "
        "false to unpin. Idempotent — calling pin twice or unpin twice is safe. "
        "Atomic — uses tmp + os.replace so a kill mid-write leaves the previous "
        "file intact. Returns {pinned: bool, filename: str} or "
        "{error: str, filename: str}."
    ),
    # S-76 introspection tools
    "depthfusion_describe_capabilities": (
        "Return which retrieval layers and capture mechanisms are engaged in this "
        "DepthFusion instance. No args required. Response: {tier, mode, flags, "
        "engaged_layers_per_op: {recall: [...], publish: [...], auto_learn: [...]}}. "
        "Useful for diagnosing silent no-ops (e.g. why graph or embedding is not "
        "contributing to recall without reading source code)."
    ),
    "depthfusion_inspect_discovery": (
        "Return parsed frontmatter of a discovery file (S-76). "
        "Args: filename (str, required) — absolute path to a discovery markdown file. "
        "Response: {filename, exists, frontmatter: {importance, salience, pinned, "
        "project, type, ...}} or {filename, exists: false, error}."
    ),
    # E-31 cognitive tools
    "depthfusion_retrieve_context": (
        "Cognitive retrieval with 8-component scoring and retrieval trace (E-31). "
        "Args: query (str), project_id (str), top_k (int, default 10), "
        "memory_types (str[], optional). Response: {memories: [...], trace: {...}}."
    ),
    "depthfusion_record_decision": (
        "Record an architectural or implementation decision as a typed memory (E-31). "
        "Args: project_id (str), decision (str), rationale (str, required non-empty), "
        "rejected_options (str[], optional), constraints (str[], optional), "
        "impact_radius (str, default 'local'), actor (str, default 'unknown'). "
        "Response: {memory_id, type, status}."
    ),
    "depthfusion_record_incident": (
        "Record an error→fix→lesson triple as operational memory (E-31). "
        "Args: project_id (str), error (str), fix (str), lesson (str), "
        "severity (str, default 'medium'), recurrence_risk (float, default 0.3), "
        "actor (str, default 'unknown'). Response: {memory_id, type, severity}."
    ),
    "depthfusion_mark_superseded": (
        "Mark a memory as superseded by a newer one (E-31). "
        "Args: project_id (str), old_memory_id (str), new_memory_id (str), "
        "reason (str), actor (str, default 'unknown'). "
        "Response: {status, old_id, new_id} or {error}."
    ),
    "depthfusion_report_outcome": (
        "Record the outcome of applying a decision or procedure (E-31). "
        "Args: project_id (str), memory_id (str), outcome (str), success (bool), "
        "actor (str, default 'unknown'). Response: {status, memory_id, success}."
    ),
    "depthfusion_get_cognitive_state": (
        "Return a summary of the current cognitive state for a project (E-31). "
        "Args: project_id (str). Response: {project_id, total_memories, "
        "active_memories, total_events, feature_flags}."
    ),
    # E-33 telemetry tools
    "depthfusion_record_telemetry": (
        "Log a per-tool-call telemetry event for cost, latency, and usage analytics "
        "(E-33 S-106/S-107). "
        "Args: session_id (str, required), tool_name (str, required), "
        "session_type (str, optional — 'agent' (default) | 'human'), "
        "agent (str, optional), project (str, optional), "
        "story_id (str, optional — backlog story ID e.g. S-106), "
        "sprint (str, optional — sprint label e.g. '2026-Q2-S1'), "
        "duration_ms (float, optional), tokens_in (int, optional), "
        "tokens_out (int, optional), cost_usd_estimate (float, optional), "
        "recorded_at (str, optional — ISO-8601 timestamp; defaults to now). "
        "Response: {ok: true, event_id: int}."
    ),
    "depthfusion_query_telemetry": (
        "Aggregate telemetry events by project, agent, story, sprint, or period (E-33 S-106). "
        "Args: project (str, optional), agent (str, optional), "
        "story_id (str, optional), sprint (str, optional), "
        "tool_name (str, optional), "
        "period (str, optional — 'day' | 'week' | 'month'; omit for totals), "
        "from_dt (str, optional — ISO-8601), to_dt (str, optional — ISO-8601). "
        "Response: {rows: [{period, event_count, session_count, "
        "total_duration_ms, avg_duration_ms, total_tokens_in, total_tokens_out, "
        "total_cost_usd}], row_count: int}."
    ),
    # E-34 S-109 skill surfacing
    "depthfusion_surface_skill_candidates": (
        "Scan telemetry for recurring tool patterns and draft candidate skills in SkillForge "
        "(E-34 S-109). "
        "Args: threshold (int, optional — min distinct sessions, default from config, usually 3), "
        "dry_run (bool, optional — default false; if true, returns candidates "
        "without POSTing to SkillForge). "
        "Response: {candidates_found: int, candidates_drafted: int, "
        "already_tracked: int, items: [{pattern_key, name, session_count, "
        "drafted, skillforge_id}]}."
    ),
    # E-35 S-111 session-start auto-recall seed; E-46 S-143 fabric_seed extension
    "depthfusion_session_seed": (
        "Run a seed recall query at session start and publish results as high-priority "
        "ContextItems tagged ['session-seed', session_id] (S-111). "
        "Args: session_id (str, required), top_k (int, optional, default 3), "
        "snippet_len (int, optional, default 800), "
        "mode (str, optional — 'recall' (default) | 'fabric_seed'), "
        "projects (str[], optional — required for fabric_seed mode), "
        "goal (str, optional — goal query for fabric_seed ranking). "
        "Response: {published: int, query: str, session_id: str} or "
        "{bundle: [...], degraded: bool, session_id: str} for fabric_seed."
    ),
    # E-45 HNSW capability ping for the agent-ops bridge
    "depthfusion_hnsw_capability": (
        "Return current HNSW index capability and state. Called by the agent-ops "
        "bridge at startup. No args. Returns HNSWCapability: "
        "{enabled, backend, model, dimension, index_path, entry_count}."
    ),
    # E-46 Event Graph Fabric tools (S-143 / T-492)
    "depthfusion_event_publish": (
        "Publish content as a MemoryEntity + EventEntity with content-hash dedup (E-46 S-143). "
        "Identical content published by N agents produces 1 MemoryEntity and N EventEntities. "
        "Args: content (str, required), agent_id (str, required), project_slug (str, required), "
        "session_id (str, optional), event_type (str, optional, default 'publish'). "
        "Response: {memory_id: str, event_id: str, deduped: bool, indexed_in_hnsw: bool}."
    ),
    "depthfusion_event_seed": (
        "Return a ranked context bundle for fabric_seed session warm-up (E-46 S-143). "
        "Ranking: recall_relevance × recency_decay × log(1 + observer_count). "
        "Args: projects (str[], required), goal (str, optional), top_k (int, optional, default 5), "
        "since_hours (float, optional, default 24). "
        "Response: {bundle: [...], degraded: bool, project_count: int}."
    ),
    "depthfusion_agent_trail": (
        "Return AGENT_PUBLISHED + AGENT_RECEIVED EventEntities for an agent (E-46 S-143). "
        "Args: agent_id (str, required), project (str, optional), "
        "since (str, optional — ISO-8601), until (str, optional — ISO-8601). "
        "Response: {trail: [{entity_id, event_type, memory_refs, first_seen, ...}], count: int}."
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
    "depthfusion_set_memory_score": None,           # always enabled (S-70)
    "depthfusion_recall_feedback": None,          # always enabled (S-72)
    "depthfusion_pin_discovery": None,            # always enabled (S-69)
    "depthfusion_describe_capabilities": None,    # always enabled (S-76)
    "depthfusion_inspect_discovery": None,        # always enabled (S-76)
    # E-31 cognitive tools
    "depthfusion_retrieve_context": "cognitive_retrieval",
    "depthfusion_record_decision": "decision_memory",
    "depthfusion_record_incident": "operational_memory",
    "depthfusion_mark_superseded": "operational_memory",
    "depthfusion_report_outcome": "operational_memory",
    "depthfusion_get_cognitive_state": None,      # always enabled
    # E-33 telemetry tools
    "depthfusion_record_telemetry": None,         # always enabled (E-33 S-106)
    "depthfusion_query_telemetry": None,          # always enabled (E-33 S-106)
    # E-34 skill surfacing
    "depthfusion_surface_skill_candidates": None, # always enabled (E-34 S-109)
    # E-35 S-111 session-start auto-recall seed
    "depthfusion_session_seed": None,             # always enabled (E-35 S-111)
    # E-45 HNSW capability ping — always enabled; returns disabled when env flag is off
    "depthfusion_hnsw_capability": None,
    # E-46 Event Graph Fabric tools — always enabled (S-143 / T-492)
    "depthfusion_event_publish": None,
    "depthfusion_event_seed": None,
    "depthfusion_agent_trail": None,
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


TOOL_SCHEMAS: dict[str, dict] = {
    "depthfusion_recall_relevant": {
        "properties": {
            "query": {"type": "string", "description": "The recall query"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "snippet_len": {"type": "integer", "minimum": 200, "maximum": 8000, "default": 1500},
            "cross_project": {"type": "boolean", "default": False},
            "project": {"type": "string"},
            "explain": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, include a structured explain block for each result "
                    "showing individual scores used to rank it"
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["full", "index", "timeline"],
                "default": "full",
                "description": (
                    "Retrieval depth: 'full' (default, scored snippets), "
                    "'index' (lightweight title+source per file, no scoring, ~10% token cost), "
                    "'timeline' (all blocks in recency order, no scoring)"
                ),
            },
        },
        "required": ["query"],
    },
    "depthfusion_confirm_discovery": {
        "properties": {
            "content": {"type": "string", "description": "The decision or fact to confirm"},
            "project": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string"},
        },
        "required": ["content"],
    },
    "depthfusion_set_memory_score": {
        "properties": {
            "filename": {
                "type": "string",
                "description": "Absolute path to a discovery markdown file",
            },
            "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "salience": {"type": "number", "minimum": 0.0, "maximum": 5.0},
        },
        "required": ["filename"],
    },
    "depthfusion_recall_feedback": {
        "properties": {
            "recall_id": {
                "type": "string",
                "description": "UUID from a prior recall_relevant response",
            },
            "used": {"type": "array", "items": {"type": "string"}, "default": []},
            "ignored": {"type": "array", "items": {"type": "string"}, "default": []},
        },
        "required": ["recall_id"],
    },
    "depthfusion_pin_discovery": {
        "properties": {
            "filename": {
                "type": "string",
                "description": "Absolute path to a discovery markdown file",
            },
            "pinned": {"type": "boolean", "default": True},
        },
        "required": ["filename"],
    },
    "depthfusion_prune_discoveries": {
        "properties": {
            "age_days": {"type": "integer", "minimum": 1, "default": 90},
            "confirm": {"type": "boolean", "default": False},
        },
        "required": [],
    },
    "depthfusion_publish_context": {
        "properties": {
            "item": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "content": {"type": "string"},
                    "source_agent": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "integer"},
                    "ttl_seconds": {"type": "integer"},
                    "metadata": {"type": "object"},
                    # S-112: structured observation fields (optional)
                    "facts": {"type": "array", "items": {"type": "string"},
                              "description": "Key facts captured in this context item"},
                    "concepts": {"type": "array", "items": {"type": "string"},
                                 "description": "Concepts / domain terms referenced"},
                    "files_read": {"type": "array", "items": {"type": "string"},
                                   "description": "Files read during this work unit"},
                    "files_modified": {"type": "array", "items": {"type": "string"},
                                       "description": "Files created or modified"},
                },
                "required": ["item_id", "content", "source_agent"],
            },
        },
        "required": ["item"],
    },
    "depthfusion_status": {
        "properties": {},
        "required": [],
    },
    "depthfusion_tag_session": {
        "properties": {
            "session_id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["session_id", "tags"],
    },
    "depthfusion_run_recursive": {
        "properties": {
            "content": {"type": "string"},
            "strategy": {"type": "string"},
        },
        "required": ["content"],
    },
    "depthfusion_tier_status": {
        "properties": {},
        "required": [],
    },
    "depthfusion_auto_learn": {
        "properties": {
            "max_files": {"type": "integer", "minimum": 1, "default": 10},
            "mode": {
                "type": "string",
                "enum": ["session", "ambient"],
                "default": "session",
                "description": (
                    "'session' (default): compress recent .tmp session files. "
                    "'ambient': publish a low-importance ambient ContextItem "
                    "(S-110 PostToolUse capture path)."
                ),
            },
            "tool_name": {
                "type": "string",
                "description": "S-110 ambient mode: name of the tool that was called",
            },
            "session_id": {
                "type": "string",
                "description": "S-110 ambient mode: current session identifier",
            },
            "files_read": {
                "type": "array",
                "items": {"type": "string"},
                "description": "S-110 ambient mode: files read by the tool call",
            },
            "files_modified": {
                "type": "array",
                "items": {"type": "string"},
                "description": "S-110 ambient mode: files written/modified by the tool call",
            },
        },
        "required": [],
    },
    "depthfusion_compress_session": {
        "properties": {
            "session_file": {"type": "string", "description": "Path to .tmp session file"},
        },
        "required": ["session_file"],
    },
    "depthfusion_graph_traverse": {
        "properties": {
            "entity": {"type": "string"},
            "depth": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
        },
        "required": ["entity"],
    },
    "depthfusion_graph_status": {
        "properties": {},
        "required": [],
    },
    "depthfusion_set_scope": {
        "properties": {
            "scope": {"type": "string", "enum": ["project", "cross_project", "global"]},
            "projects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Project slugs to activate (used when scope=project).",
            },
            "sub_scope": {
                "type": "string",
                "description": (
                    "Optional Room label. Restricts recall to blocks whose "
                    "sub_scope frontmatter matches, plus all unlabelled blocks. "
                    "Omit or send empty string to disable Room filtering."
                ),
            },
        },
        "required": ["scope"],
    },
    "depthfusion_inspect_discovery": {
        "properties": {
            "filename": {"type": "string"},
        },
        "required": ["filename"],
    },
    "depthfusion_describe_capabilities": {
        "properties": {},
        "required": [],
    },
    # E-31 cognitive tools
    "depthfusion_retrieve_context": {
        "properties": {
            "query": {"type": "string"},
            "project_id": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "memory_types": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["query", "project_id"],
    },
    "depthfusion_record_decision": {
        "properties": {
            "project_id": {"type": "string"},
            "decision": {"type": "string"},
            "rationale": {"type": "string"},
            "rejected_options": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "impact_radius": {"type": "string", "default": "local"},
            "actor": {"type": "string", "default": "unknown"},
        },
        "required": ["project_id", "decision", "rationale"],
    },
    "depthfusion_record_incident": {
        "properties": {
            "project_id": {"type": "string"},
            "error": {"type": "string"},
            "fix": {"type": "string"},
            "lesson": {"type": "string"},
            "severity": {"type": "string", "default": "medium"},
            "recurrence_risk": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.3},
            "actor": {"type": "string", "default": "unknown"},
        },
        "required": ["project_id", "error", "fix", "lesson"],
    },
    "depthfusion_mark_superseded": {
        "properties": {
            "project_id": {"type": "string"},
            "old_memory_id": {"type": "string"},
            "new_memory_id": {"type": "string"},
            "reason": {"type": "string"},
            "actor": {"type": "string", "default": "unknown"},
        },
        "required": ["project_id", "old_memory_id", "new_memory_id", "reason"],
    },
    "depthfusion_report_outcome": {
        "properties": {
            "project_id": {"type": "string"},
            "memory_id": {"type": "string"},
            "outcome": {"type": "string"},
            "success": {"type": "boolean"},
            "actor": {"type": "string", "default": "unknown"},
        },
        "required": ["project_id", "memory_id", "outcome", "success"],
    },
    "depthfusion_get_cognitive_state": {
        "properties": {
            "project_id": {"type": "string"},
        },
        "required": ["project_id"],
    },
    # E-33 telemetry tools
    "depthfusion_record_telemetry": {
        "properties": {
            "session_id": {"type": "string"},
            "tool_name": {"type": "string"},
            "session_type": {"type": "string", "enum": ["agent", "human"], "default": "agent"},
            "agent": {"type": "string"},
            "project": {"type": "string"},
            "story_id": {"type": "string"},
            "sprint": {"type": "string"},
            "duration_ms": {"type": "number"},
            "tokens_in": {"type": "integer"},
            "tokens_out": {"type": "integer"},
            "cost_usd_estimate": {"type": "number"},
            "recorded_at": {"type": "string"},
        },
        "required": ["session_id", "tool_name"],
    },
    "depthfusion_query_telemetry": {
        "properties": {
            "project": {"type": "string"},
            "agent": {"type": "string"},
            "session_type": {"type": "string", "enum": ["agent", "human"]},
            "story_id": {"type": "string"},
            "sprint": {"type": "string"},
            "tool_name": {"type": "string"},
            "period": {"type": "string", "enum": ["day", "week", "month"]},
            "from_dt": {"type": "string"},
            "to_dt": {"type": "string"},
        },
        "required": [],
    },
    # E-34 S-109
    "depthfusion_surface_skill_candidates": {
        "properties": {
            "threshold": {
                "type": "integer",
                "description": "Min distinct sessions (default from config)",
            },
            "dry_run": {"type": "boolean", "default": False},
        },
        "required": [],
    },
    # E-35 S-111 session-start auto-recall seed; E-46 S-143 fabric_seed extension
    "depthfusion_session_seed": {
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Claude Code session ID (from SessionStart hook payload)",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 3,
                "description": "Maximum seed items to publish",
            },
            "snippet_len": {
                "type": "integer",
                "minimum": 200,
                "maximum": 2000,
                "default": 800,
                "description": "Maximum snippet length per seed item",
            },
            "mode": {
                "type": "string",
                "enum": ["recall", "fabric_seed"],
                "default": "recall",
                "description": "'recall' (default) or 'fabric_seed' for Event Graph Fabric warm-up",
            },
            "projects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Project slugs (required for fabric_seed mode)",
            },
            "goal": {
                "type": "string",
                "description": "Goal query for recall_relevance ranking in fabric_seed mode",
            },
        },
        "required": ["session_id"],
    },
    # E-45 HNSW capability ping
    "depthfusion_hnsw_capability": {
        "properties": {},
        "required": [],
    },
    # E-46 Event Graph Fabric tools (S-143 / T-492)
    "depthfusion_event_publish": {
        "properties": {
            "content": {"type": "string", "description": "Memory content to publish"},
            "agent_id": {"type": "string", "description": "Publishing agent identifier"},
            "project_slug": {"type": "string", "description": "Project namespace"},
            "session_id": {"type": "string"},
            "event_type": {"type": "string", "default": "publish"},
        },
        "required": ["content", "agent_id", "project_slug"],
    },
    "depthfusion_event_seed": {
        "properties": {
            "projects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Project slugs to query",
            },
            "goal": {"type": "string", "description": "Goal query for recall_relevance ranking"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "since_hours": {"type": "number", "minimum": 1, "maximum": 720, "default": 24},
        },
        "required": ["projects"],
    },
    "depthfusion_agent_trail": {
        "properties": {
            "agent_id": {"type": "string", "description": "Agent to look up"},
            "project": {"type": "string", "description": "Filter by project slug"},
            "since": {"type": "string", "description": "ISO-8601 lower bound"},
            "until": {"type": "string", "description": "ISO-8601 upper bound"},
        },
        "required": ["agent_id"],
    },
}


def _make_tool_schema(name: str, description: str) -> dict:
    """Build an MCP tool schema with explicit JSON Schema properties."""
    schema = TOOL_SCHEMAS.get(name, {"properties": {}, "required": []})
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": schema.get("properties", {}),
            "required": schema.get("required", []),
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
        return _tool_publish_context(arguments, config)
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
    elif tool_name == "depthfusion_set_memory_score":
        return _tool_set_memory_score(arguments)
    elif tool_name == "depthfusion_prune_discoveries":
        return _tool_prune_discoveries(arguments)
    elif tool_name == "depthfusion_recall_feedback":
        return _tool_recall_feedback(arguments)
    elif tool_name == "depthfusion_pin_discovery":
        return _tool_pin_discovery(arguments)
    elif tool_name == "depthfusion_describe_capabilities":
        return _tool_describe_capabilities()
    elif tool_name == "depthfusion_inspect_discovery":
        return _tool_inspect_discovery(arguments)
    elif tool_name == "depthfusion_retrieve_context":
        return _tool_retrieve_context(arguments, config)
    elif tool_name == "depthfusion_record_decision":
        return _tool_record_decision(arguments, config)
    elif tool_name == "depthfusion_record_incident":
        return _tool_record_incident(arguments, config)
    elif tool_name == "depthfusion_mark_superseded":
        return _tool_mark_superseded(arguments, config)
    elif tool_name == "depthfusion_report_outcome":
        return _tool_report_outcome(arguments, config)
    elif tool_name == "depthfusion_get_cognitive_state":
        return _tool_get_cognitive_state(arguments, config)
    elif tool_name == "depthfusion_record_telemetry":
        return _tool_record_telemetry(arguments, config)
    elif tool_name == "depthfusion_query_telemetry":
        return _tool_query_telemetry(arguments, config)
    elif tool_name == "depthfusion_surface_skill_candidates":
        return _tool_surface_skill_candidates(arguments, config)
    elif tool_name == "depthfusion_session_seed":
        return _tool_session_seed(arguments)
    elif tool_name == "depthfusion_hnsw_capability":
        return _tool_hnsw_capability()
    elif tool_name == "depthfusion_event_publish":
        return _tool_event_publish(arguments)
    elif tool_name == "depthfusion_event_seed":
        return _tool_event_seed(arguments)
    elif tool_name == "depthfusion_agent_trail":
        return _tool_agent_trail(arguments)
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
    "rule": 0.95,       # user-defined conventions and standards — high authority
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


def _tool_tag_session(arguments: dict) -> str:
    session_id = arguments.get("session_id", "")
    tags = arguments.get("tags", [])
    return json.dumps({"session_id": session_id, "tags": tags, "tagged": True})


# Module-level ContextBus cache (S-78). Patchable for tests:
#   patch.object(mcp_server, "_get_context_bus", return_value=test_bus, create=True)
_BUS_INSTANCE: ContextBus | None = None


def _get_context_bus(config: Any = None) -> ContextBus:
    """Return the process-wide ContextBus, lazily constructed from config.

    The instance is cached on first call to avoid rebuilding FileBus's hash
    index on every MCP request. Tests should patch this function directly via
    ``unittest.mock.patch.object(..., create=True)`` rather than mutate the
    cache. ``config`` may be ``None`` — defaults are used (file backend at
    ``~/.claude/context-bus``).
    """
    global _BUS_INSTANCE
    if _BUS_INSTANCE is not None:
        return _BUS_INSTANCE

    backend = getattr(config, "bus_backend", "file")
    bus_dir_str = getattr(config, "bus_file_dir", "~/.claude/context-bus")
    if backend == "memory":
        _BUS_INSTANCE = InMemoryBus()
    else:
        _BUS_INSTANCE = FileBus(bus_dir=Path(bus_dir_str).expanduser())
    return _BUS_INSTANCE


# ---------------------------------------------------------------------------
# E-45 HNSW vector index — module-level singleton (lazy init, env-gated)
# ---------------------------------------------------------------------------

_HNSW_STORE: Any = None  # depthfusion.retrieval.hnsw_store.HNSWStore | None
_HNSW_INIT_ATTEMPTED: bool = False
_HNSW_SHUTDOWN_REGISTERED: bool = False
_HNSW_LOCK = threading.Lock()


def _hnsw_enabled() -> bool:
    return os.environ.get("DEPTHFUSION_HNSW_ENABLED", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _register_hnsw_shutdown() -> None:
    """Install SIGTERM/SIGINT handlers that flush the index on graceful exit."""
    global _HNSW_SHUTDOWN_REGISTERED
    if _HNSW_SHUTDOWN_REGISTERED:
        return
    try:
        import signal as _signal

        def _hnsw_shutdown_handler(signum, frame):  # type: ignore[no-redef]
            store = _HNSW_STORE
            if store is not None:
                try:
                    store.save()
                    logger.info("[hnsw] index persisted on shutdown")
                except Exception as exc:  # noqa: BLE001 — best-effort flush
                    logger.warning("[hnsw] failed to persist on shutdown: %s", exc)

        # Only install handlers in the main thread (signal API restriction).
        if threading.current_thread() is threading.main_thread():
            _signal.signal(_signal.SIGTERM, _hnsw_shutdown_handler)
            _signal.signal(_signal.SIGINT, _hnsw_shutdown_handler)
            _HNSW_SHUTDOWN_REGISTERED = True
    except (ValueError, OSError) as exc:
        # signal() raises ValueError outside the main thread or when running
        # under restricted environments — degrade silently.
        logger.debug("[hnsw] shutdown handler not installed: %s", exc)


def _get_hnsw_store() -> Any:
    """Return the process-wide HNSWStore (lazily constructed), or None.

    Returns None when ``DEPTHFUSION_HNSW_ENABLED`` is falsey or when the
    store could not be initialised (missing hnswlib, embedding-model load
    failure, etc.). The init attempt is only made once per process — on
    subsequent calls a failed init still returns None without retrying.
    """
    global _HNSW_STORE, _HNSW_INIT_ATTEMPTED
    if not _hnsw_enabled():
        return None
    if _HNSW_INIT_ATTEMPTED:
        return _HNSW_STORE

    with _HNSW_LOCK:
        if _HNSW_INIT_ATTEMPTED:
            return _HNSW_STORE
        _HNSW_INIT_ATTEMPTED = True
        try:
            from depthfusion.retrieval.hnsw_store import HNSWStore

            index_path_raw = os.environ.get(
                "DEPTHFUSION_HNSW_INDEX_PATH",
                "~/.agent-mc/depthfusion/hnsw.bin",
            )
            model_name = (
                os.environ.get("DEPTHFUSION_EMBEDDING_MODEL", "").strip()
                or "all-MiniLM-L6-v2"
            )
            store = HNSWStore(
                index_path=Path(index_path_raw).expanduser(),
                model_name=model_name,
            )
            if not getattr(store, "hnsw_ready", False):
                logger.info("[hnsw] store not ready — falling back to BM25-only")
                _HNSW_STORE = None
                return None
            _HNSW_STORE = store
            _register_hnsw_shutdown()
            logger.info("[hnsw] store initialised (model=%s)", model_name)
            return _HNSW_STORE
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.info("[hnsw] init failed (%s) — falling back to BM25-only", exc)
            _HNSW_STORE = None
            return None


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


def _tool_publish_context(arguments: dict, config: Any = None) -> str:
    """Publish a context item to the bus with idempotent dedup (S-78).

    Returns JSON of the canonical publish-result shape:
      ``{"published": True, "item_id": <id>, "deduped": <bool>}``
    On dedup, ``item_id`` is the ORIGINAL stored item's id, not the retry's.
    """
    item_payload = arguments.get("item")
    if not isinstance(item_payload, dict):
        return json.dumps(
            {"error": "publish_context: 'item' must be an object", "published": False}
        )
    try:
        item = ContextItem(
            item_id=item_payload["item_id"],
            content=item_payload["content"],
            source_agent=item_payload["source_agent"],
            tags=list(item_payload.get("tags", [])),
            priority=item_payload.get("priority", "normal"),
            ttl_seconds=item_payload.get("ttl_seconds"),
            metadata=item_payload.get("metadata", {}),
            # S-70 — operator-supplied scoring (optional, defaults via
            # ContextItem.__post_init__). Unsupplied → canonical defaults.
            importance=item_payload.get("importance"),
            salience=item_payload.get("salience"),
            # S-112: structured observation fields (optional; default empty)
            facts=list(item_payload.get("facts") or []),
            concepts=list(item_payload.get("concepts") or []),
            files_read=list(item_payload.get("files_read") or []),
            files_modified=list(item_payload.get("files_modified") or []),
        )
    except (KeyError, TypeError) as exc:
        return json.dumps(
            {"error": f"publish_context: invalid item payload: {exc}", "published": False}
        )

    bus = _get_context_bus(config)
    try:
        result = bus.publish(item)
    except Exception as exc:  # noqa: BLE001 — surface bus errors verbatim
        return json.dumps(
            {"error": f"publish_context: bus error: {exc}", "published": False}
        )

    # S-73: emit event on first publish of a high-importance item (skip dedup retries)
    if isinstance(result, dict) and not result.get("deduped", False):
        _cfg = config if config is not None else type("_C", (), {
            "high_importance_threshold": 0.8,
            "event_log": "~/.claude/shared/depthfusion-events.jsonl",
        })()
        emit_if_high_importance(
            item,
            event_log=getattr(_cfg, "event_log", "~/.claude/shared/depthfusion-events.jsonl"),
            threshold=getattr(_cfg, "high_importance_threshold", 0.8),
        )

    # E-45: HNSW upsert behind feature flag; never blocks the BM25/bus path.
    indexed_in_hnsw = False
    store = _get_hnsw_store()
    if store is not None:
        try:
            indexed_in_hnsw = bool(store.upsert(item.item_id, item.content))
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.debug("[hnsw] upsert failed for %s: %s", item.item_id, exc)
            indexed_in_hnsw = False

    if isinstance(result, dict):
        result["indexed_in_hnsw"] = indexed_in_hnsw
    else:
        result = {"indexed_in_hnsw": indexed_in_hnsw, "result": result}
    return json.dumps(result)


def _tool_run_recursive(arguments: dict, config: Any) -> str:
    query = arguments.get("query", "")
    content = arguments.get("content", "")
    try:
        from depthfusion.recursive.client import RLMClient
        client = RLMClient(config=config)
        if not client.is_skillforge_configured() and not client.is_available():
            return json.dumps({"error": "neither SkillForge nor rlm configured", "result": None})
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
    """Trigger auto-learn: session compression or ambient capture (S-110)."""
    mode = arguments.get("mode", "session")
    if mode == "ambient":
        return _handle_ambient_capture(arguments)

    from pathlib import Path
    max_files = min(int(arguments.get("max_files", 5)), 50)
    project = arguments.get("project", "")
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.exists():
        return json.dumps({"compressed": 0, "message": "No sessions directory"})

    # S-74 fix: obtain graph_store once if graph extraction is enabled.
    # summarize_and_extract_graph is internally gated — safe to call always.
    graph_store = None
    if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true":
        try:
            from depthfusion.graph.store import get_store as _get_graph_store
            graph_store = _get_graph_store()
        except Exception:
            pass

    try:
        from depthfusion.capture.auto_learn import summarize_and_extract_graph
        from depthfusion.capture.compressor import SessionCompressor
        compressor = SessionCompressor()
        recent = sorted(sessions_dir.glob("*.tmp"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
        results = []
        for tmp in recent:
            out = compressor.compress(tmp)
            if out:
                results.append(str(out.name))
                summarize_and_extract_graph(out, project, graph_store)
        return json.dumps({
            "compressed": len(results),
            "files": results,
            "message": f"Auto-learned from {len(results)} session files",
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "compressed": 0})


def _handle_ambient_capture(arguments: dict) -> str:
    """S-110: publish a low-importance ambient ContextItem to the FileBus."""
    tool_name = arguments.get("tool_name", "")
    session_id = arguments.get("session_id", "unknown")
    files_read = list(arguments.get("files_read") or [])
    files_modified = list(arguments.get("files_modified") or [])

    if not tool_name:
        return json.dumps({"error": "tool_name required for ambient mode", "published": False})

    try:
        from pathlib import Path

        from depthfusion.capture.auto_learn import build_ambient_item
        from depthfusion.router.bus import FileBus

        item = build_ambient_item(
            tool_name=tool_name,
            session_id=session_id,
            files_read=files_read,
            files_modified=files_modified,
        )
        bus_dir = Path(
            os.environ.get("DEPTHFUSION_BUS_FILE_DIR", "~/.claude/context-bus")
        ).expanduser()
        bus_dir.mkdir(parents=True, exist_ok=True)
        bus = FileBus(bus_dir=bus_dir)
        result = bus.publish(item)
        return json.dumps({"published": result.get("published", False), "item_id": item.item_id})
    except Exception as exc:
        return json.dumps({"error": str(exc), "published": False})


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

    haiku_enabled = os.environ.get("DEPTHFUSION_HAIKU_ENABLED", "false").lower() == "true"
    return json.dumps({
        "graph_enabled": True,
        "node_count": store.node_count(),
        "edge_count": store.edge_count(),
        "entities_by_type": type_breakdown,
        "tier": os.environ.get("DEPTHFUSION_MODE", "local"),
        "extraction_active": haiku_enabled,   # S-74: graph extraction requires HAIKU_ENABLED
        "tier_gates_extraction": False,        # no tier gate — runs on any tier when flags set
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


def _tool_set_memory_score(arguments: dict) -> str:
    """E-27 / S-70 — operator override of importance/salience on a discovery.

    Atomic, lock-serialized read-modify-write:
      1. Acquire ``fcntl.LOCK_EX`` on a sidecar lock file
         (``<target>.scorelock``). Holds for the entire RMW critical
         section so two concurrent partial updates (caller A supplies
         only ``importance``, caller B supplies only ``salience``) cannot
         each read a stale version and silently overwrite the other's
         change. Mirrors the S-78 ``FileBus`` flock pattern.
      2. Re-read the file under lock; parse existing scoring frontmatter.
      3. For each unsupplied field, preserve the file's current value;
         for each supplied field, validate / clamp via ``MemoryScore``.
      4. Splice the new scalars into the frontmatter block.
      5. Write to a unique ``mkstemp`` sibling, ``fsync``, then
         ``os.replace`` over the target. ``os.replace`` is atomic on
         POSIX — a kill mid-write leaves the previous file intact.
      6. Release the lock.

    Idempotent: replaying the same payload produces byte-identical file
    content (provided no concurrent writer ran between calls). Out-of-
    range values are clamped via ``MemoryScore.__post_init__``.
    """
    filename = arguments.get("filename")
    importance = arguments.get("importance")
    salience = arguments.get("salience")

    if not isinstance(filename, str) or not filename.strip():
        return json.dumps({
            "ok": False,
            "error": "set_memory_score: 'filename' must be a non-empty string",
        })
    if importance is None and salience is None:
        return json.dumps({
            "ok": False,
            "error": "set_memory_score: at least one of 'importance' or "
                     "'salience' must be supplied",
        })

    # Type-validate scalar inputs at the boundary so a JSON-RPC client
    # passing strings (e.g. `"0.88"`) gets the documented error shape
    # rather than a TypeError bubbling out of MemoryScore.__post_init__.
    for fname, fval in (("importance", importance), ("salience", salience)):
        if fval is not None and not isinstance(fval, (int, float)):
            return json.dumps({
                "ok": False,
                "error": f"set_memory_score: '{fname}' must be a number, "
                         f"got {type(fval).__name__}",
            })

    target = Path(filename).expanduser()
    if not target.exists():
        return json.dumps({
            "ok": False,
            "error": f"set_memory_score: file not found: {filename}",
        })
    if not target.is_file():
        return json.dumps({
            "ok": False,
            "error": f"set_memory_score: not a regular file: {filename}",
        })

    from depthfusion.capture.dedup import extract_memory_score
    from depthfusion.core.file_locking import atomic_frontmatter_rewrite
    from depthfusion.core.types import MemoryScore

    try:
        with atomic_frontmatter_rewrite(target) as ctx:
            existing = extract_memory_score(ctx.body)
            final_imp = existing.importance if importance is None else importance
            final_sal = existing.salience if salience is None else salience
            normalized = MemoryScore(importance=final_imp, salience=final_sal)
            ctx.set_score(
                importance=normalized.importance,
                salience=normalized.salience,
            )
    except FileNotFoundError:
        return json.dumps({
            "ok": False, "error": f"set_memory_score: file not found: {filename}",
        })
    except OSError as exc:
        return json.dumps({
            "ok": False, "error": f"set_memory_score: write failed: {exc}",
        })
    except Exception as exc:  # noqa: BLE001 — MCP tool boundary; must not raise
        return json.dumps({
            "ok": False, "error": f"set_memory_score: unexpected error: {exc}",
        })

    return json.dumps({
        "ok": True,
        "filename": str(target),
        "importance": normalized.importance,
        "salience": normalized.salience,
    })


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


def _tool_pin_discovery(arguments: dict) -> str:
    """S-69 — pin or unpin a discovery file to exempt it from age-based pruning.

    Atomic, lock-serialized read-modify-write (same pattern as
    ``_tool_set_memory_score``):
      1. Validate ``filename`` and ``pinned`` arguments.
      2. Resolve the path; return a structured error if not found.
      3. Acquire the sidecar lock and splice ``pinned: true/false`` into
         the YAML frontmatter via ``_splice_pin_frontmatter``.
      4. Write via mkstemp + os.replace.

    Idempotent: calling pin twice or unpin twice produces the same result.
    Missing file: returns ``{"error": "file not found", "filename": str}``
    (does NOT raise).
    """
    filename = arguments.get("filename")
    pinned_raw = arguments.get("pinned", True)

    if not isinstance(filename, str) or not filename.strip():
        return json.dumps({
            "error": "pin_discovery: 'filename' must be a non-empty string",
            "filename": filename,
        })
    if not isinstance(pinned_raw, bool):
        return json.dumps({
            "error": (
                f"pin_discovery: 'pinned' must be a bool, "
                f"got {type(pinned_raw).__name__}"
            ),
            "filename": filename,
        })

    target = Path(filename).expanduser()
    if not target.exists():
        return json.dumps({
            "error": "file not found",
            "filename": filename,
        })
    if not target.is_file():
        return json.dumps({
            "error": "not a regular file",
            "filename": filename,
        })

    from depthfusion.core.file_locking import atomic_frontmatter_rewrite

    try:
        with atomic_frontmatter_rewrite(target) as ctx:
            ctx.set_pinned(pinned_raw)
    except FileNotFoundError:
        return json.dumps({
            "error": "file not found",
            "filename": filename,
        })
    except OSError as exc:
        return json.dumps({
            "error": f"write failed: {exc}",
            "filename": filename,
        })
    except Exception as exc:  # noqa: BLE001 — MCP tool boundary; must not raise
        return json.dumps({
            "error": f"unexpected error: {exc}",
            "filename": filename,
        })

    return json.dumps({
        "pinned": pinned_raw,
        "filename": str(target),
    })


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


def _tool_inspect_discovery(arguments: dict) -> str:
    """S-76: return parsed frontmatter of a discovery file."""
    import re as _re
    filename = arguments.get("filename", "")
    if not filename:
        return json.dumps({"error": "filename argument required", "exists": False})

    target = Path(os.path.expanduser(filename))
    if not target.exists():
        return json.dumps({"filename": str(target), "exists": False, "error": "file not found"})

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return json.dumps({"filename": str(target), "exists": True, "error": str(exc)})

    frontmatter: dict = {}
    fm_match = _re.match(r"^---\s*\n(.*?)\n---", text, _re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            kv = line.split(":", 1)
            if len(kv) == 2:
                key = kv[0].strip()
                val = kv[1].strip()
                # coerce common scalar types
                if val.lower() in ("true", "false"):
                    frontmatter[key] = val.lower() == "true"
                else:
                    try:
                        frontmatter[key] = float(val) if "." in val else int(val)
                    except ValueError:
                        frontmatter[key] = val

    return json.dumps({
        "filename": str(target),
        "exists": True,
        "frontmatter": frontmatter,
    }, indent=2)


def _tool_set_scope(arguments: dict) -> str:
    """Programmatically set session graph scope."""
    from datetime import datetime, timezone

    from depthfusion.graph.scope import write_scope
    from depthfusion.graph.types import GraphScope

    # Read `scope` (schema-advertised key); fall back to `mode` for back-compat.
    mode = arguments.get("scope") or arguments.get("mode") or "project"
    projects = arguments.get("projects") or []

    if mode not in ("project", "cross_project", "global"):
        return json.dumps({"error": f"Invalid scope: {mode}. Use project|cross_project|global"})

    # ADR-001: sub_scope is ORTHOGONAL to mode — never cleared by mode value.
    sub_scope_raw = arguments.get("sub_scope")
    sub_scope: str | None = None
    if sub_scope_raw is not None:
        s = str(sub_scope_raw).strip()
        sub_scope = s or None  # empty/whitespace -> None (Room filtering off)

    scope = GraphScope(
        mode=mode,
        active_projects=projects,
        session_id="mcp_set",
        set_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        sub_scope=sub_scope,  # NEW — ADR-001
    )
    write_scope(scope)
    return json.dumps({
        "ok": True,
        "mode": mode,
        "active_projects": projects,
        "sub_scope": sub_scope,  # NEW — echo resolved value (None if off)
    })


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


## ---------------------------------------------------------------------------
## E-31 Cognitive tool implementations
## ---------------------------------------------------------------------------

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


def _tool_record_decision(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.mcp.cognitive_tools import build_decision_memory
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    actor = arguments.get("actor", "unknown")
    m = build_decision_memory(
        project_id=project_id,
        decision=arguments.get("decision", ""),
        rationale=arguments.get("rationale", ""),
        actor=actor,
        rejected_options=arguments.get("rejected_options"),
        constraints=arguments.get("constraints"),
        impact_radius=arguments.get("impact_radius", "local"),
    )
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=m.id,
        event_type=MemoryEventType.CREATED,
        project_id=project_id,
        payload=m.to_dict(),
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    EventLog(config.event_log_path).append(event)
    MemoryStore(config.memory_store_path).upsert(m)
    return json.dumps({"memory_id": m.id, "type": "decision", "status": "recorded"})


def _tool_record_incident(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.mcp.cognitive_tools import build_incident_memory
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    actor = arguments.get("actor", "unknown")
    severity = arguments.get("severity", "medium")
    m = build_incident_memory(
        project_id=project_id,
        error=arguments.get("error", ""),
        fix=arguments.get("fix", ""),
        lesson=arguments.get("lesson", ""),
        actor=actor,
        severity=severity,
        recurrence_risk=float(arguments.get("recurrence_risk", 0.3)),
    )
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=m.id,
        event_type=MemoryEventType.CREATED,
        project_id=project_id,
        payload=m.to_dict(),
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    EventLog(config.event_log_path).append(event)
    MemoryStore(config.memory_store_path).upsert(m)
    return json.dumps({"memory_id": m.id, "type": "operational", "severity": severity})


def _tool_mark_superseded(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.core.memory_object import MemoryStatus
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    old_id = arguments.get("old_memory_id", "")
    new_id = arguments.get("new_memory_id", "")
    reason = arguments.get("reason", "")
    actor = arguments.get("actor", "unknown")

    store = MemoryStore(config.memory_store_path)
    log = EventLog(config.event_log_path)
    old = store.get(old_id)
    if not old:
        return json.dumps({"error": f"memory {old_id} not found"})
    old.status = MemoryStatus.SUPERSEDED
    old.extra["superseded_by"] = new_id
    old.extra["superseded_reason"] = reason
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=old_id,
        event_type=MemoryEventType.SUPERSEDED,
        project_id=project_id,
        payload={"new_id": new_id, "reason": reason},
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    log.append(event)
    store.upsert(old)
    return json.dumps({"status": "superseded", "old_id": old_id, "new_id": new_id})


def _tool_report_outcome(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    memory_id = arguments.get("memory_id", "")
    outcome = arguments.get("outcome", "")
    success = bool(arguments.get("success", False))
    actor = arguments.get("actor", "unknown")

    store = MemoryStore(config.memory_store_path)
    log = EventLog(config.event_log_path)
    m = store.get(memory_id)
    if not m:
        return json.dumps({"error": f"memory {memory_id} not found"})
    outcomes = m.extra.get("outcomes", [])
    outcomes.append({
        "outcome": outcome,
        "success": success,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    m.extra["outcomes"] = outcomes
    if success:
        m.confidence.verification_count += 1
        m.confidence.score = min(1.0, m.confidence.score + 0.05)
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=memory_id,
        event_type=MemoryEventType.OUTCOME_RECORDED,
        project_id=project_id,
        payload={"outcome": outcome, "success": success},
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    log.append(event)
    store.upsert(m)
    return json.dumps({"status": "recorded", "memory_id": memory_id, "success": success})


def _tool_get_cognitive_state(arguments: dict, config: Any) -> str:
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    store = MemoryStore(config.memory_store_path)
    log = EventLog(config.event_log_path)
    total = store.count(project_id or None)
    active = len(store.query(project_id=project_id or None, limit=1000))
    return json.dumps({
        "project_id": project_id,
        "total_memories": total,
        "active_memories": active,
        "total_events": log.count(),
        "feature_flags": {
            "cognitive_retrieval": getattr(config, "cognitive_retrieval", False),
            "contradiction_engine": getattr(config, "contradiction_engine", False),
            "decision_memory": getattr(config, "decision_memory", False),
            "operational_memory": getattr(config, "operational_memory", False),
            "autonomic": getattr(config, "autonomic", False),
        },
    })


def _autonomic_consolidation_loop(config: Any) -> None:
    """Background daemon thread: find near-duplicate and archive candidates.

    Activated when DEPTHFUSION_AUTONOMIC=1. Never crashes — all exceptions
    are caught so the MCP server remains stable regardless of consolidation
    failures.

    Env:
        DEPTHFUSION_CONSOLIDATION_INTERVAL_MINUTES  (default 30)
    """
    try:
        interval_minutes = int(
            os.getenv("DEPTHFUSION_CONSOLIDATION_INTERVAL_MINUTES", "30")
        )
    except ValueError:
        logger.warning(
            "[autonomic] invalid DEPTHFUSION_CONSOLIDATION_INTERVAL_MINUTES, "
            "defaulting to 30 minutes"
        )
        interval_minutes = 30
    interval_seconds = interval_minutes * 60
    logger.info(
        "[autonomic] consolidation scheduler started (interval=%dm)", interval_minutes
    )

    while True:
        try:
            # Lazy imports inside the loop — keeps module-level import surface clean
            from depthfusion.cognitive.consolidator import MemoryConsolidator
            from depthfusion.storage.memory_store import MemoryStore

            store = MemoryStore(config.memory_store_path)
            memories = store.query(limit=2000)

            consolidator = MemoryConsolidator()

            merge_result = consolidator.find_near_duplicates(memories)
            for src_id, target_id in merge_result.merge_candidates:
                logger.info(
                    "[autonomic] merge candidate: %s → %s", src_id, target_id
                )

            archive_result = consolidator.find_archive_candidates(memories)
            for mem in archive_result.archive_candidates:
                logger.info("[autonomic] archive candidate: %s", mem.id)

            if not merge_result.merge_candidates and not archive_result.archive_candidates:
                logger.debug("[autonomic] consolidation pass complete — no candidates")
            else:
                logger.info(
                    "[autonomic] consolidation pass complete — %d merge, %d archive",
                    len(merge_result.merge_candidates),
                    len(archive_result.archive_candidates),
                )
        except Exception as exc:  # noqa: BLE001 — must never crash the server
            logger.warning("[autonomic] consolidation error (continuing): %s", exc)

        time.sleep(interval_seconds)


def main() -> None:
    """MCP server entry point.

    Reads config from env, registers enabled tools, serves over stdio (JSON-RPC).
    """
    from depthfusion.core.config import DepthFusionConfig

    config = DepthFusionConfig.from_env()
    enabled = get_enabled_tools(config)
    logger.info(f"DepthFusion MCP server starting — {len(enabled)} tools enabled")
    _emit_startup_event(len(enabled))

    import os as _os

    from depthfusion.utils.mode import normalise_mode
    _check_backend_health(normalise_mode(_os.environ.get("DEPTHFUSION_MODE")))

    if os.getenv("DEPTHFUSION_AUTONOMIC", "0") == "1":
        t = threading.Thread(
            target=_autonomic_consolidation_loop,
            args=(config,),
            daemon=True,
            name="depthfusion-autonomic",
        )
        t.start()

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


def _tool_session_seed(arguments: dict) -> str:
    """Publish top recall results as high-priority session-seed ContextItems (S-111/S-143)."""
    import asyncio
    from pathlib import Path

    session_id = arguments.get("session_id", "unknown")
    mode = arguments.get("mode", "recall")

    if not session_id:
        return json.dumps({"error": "session_id required", "published": 0})

    if mode == "fabric_seed":
        projects = arguments.get("projects") or []
        if not projects:
            return json.dumps({
                "error": "projects required for fabric_seed mode",
                "session_id": session_id,
            })
        goal = arguments.get("goal", "")
        top_k = int(arguments.get("top_k", 5))
        since_hours = float(arguments.get("since_hours", 24.0))
        try:
            store = _get_fabric_store()
            result = asyncio.run(
                store.fabric_seed_bundle(
                    projects=projects,
                    goal=goal,
                    top_k=top_k,
                    since_hours=since_hours,
                )
            )
            result["session_id"] = session_id
            return json.dumps(result)
        except Exception as exc:
            return json.dumps({
                "error": str(exc), "session_id": session_id, "bundle": [], "degraded": True,
            })

    # Default: recall mode (S-111)
    from depthfusion.hooks.session_start import (
        _build_seed_query,
        _detect_project_name,
        _recall_and_seed,
        _recent_git_messages,
    )

    top_k = int(arguments.get("top_k", 3))
    snippet_len = int(arguments.get("snippet_len", 800))

    try:
        cwd = Path.cwd()
        project_name = _detect_project_name(cwd)
        git_messages = _recent_git_messages(cwd)
        query = _build_seed_query(project_name, git_messages)
        published = _recall_and_seed(session_id, top_k=top_k, snippet_len=snippet_len)
        return json.dumps({
            "published": published,
            "query": query,
            "session_id": session_id,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "published": 0, "session_id": session_id})


# ---------------------------------------------------------------------------
# E-46 Event Graph Fabric tool helpers
# ---------------------------------------------------------------------------

_fabric_store = None


def _get_fabric_store():
    """Lazy singleton EventStore for MCP tool calls (sync init, async methods)."""
    global _fabric_store
    if _fabric_store is None:
        from depthfusion.core.event_store import EventStore, RedisStreamBackend
        from depthfusion.graph.store import get_store

        graph = get_store()
        redis_url = os.getenv("DEPTHFUSION_REDIS_URL", "")
        stream = RedisStreamBackend(redis_url) if redis_url else None
        _fabric_store = EventStore(graph=graph, stream=stream)
    return _fabric_store


def _tool_event_publish(arguments: dict) -> str:
    """Publish content as a MemoryEntity + EventEntity with content-hash dedup (E-46 S-143)."""
    import asyncio

    content = arguments.get("content", "")
    agent_id = arguments.get("agent_id", "")
    project_slug = arguments.get("project_slug", "")

    if not content:
        return json.dumps({"error": "content required"})
    if not agent_id:
        return json.dumps({"error": "agent_id required"})
    if not project_slug:
        return json.dumps({"error": "project_slug required"})

    session_id = arguments.get("session_id") or None
    event_type = arguments.get("event_type", "publish")

    try:
        store = _get_fabric_store()
        result = asyncio.run(
            store.publish_memory(
                content=content,
                agent_id=agent_id,
                project_slug=project_slug,
                event_type=event_type,
                session_id=session_id,
            )
        )
        indexed_in_hnsw = False
        if not result.get("deduped"):
            hnsw = _get_hnsw_store()
            if hnsw is not None:
                try:
                    hnsw.upsert(entity_id=result["memory_id"], text=content, project=project_slug)
                    indexed_in_hnsw = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[event_publish] HNSW upsert failed (non-fatal): %s", exc)
        result["indexed_in_hnsw"] = indexed_in_hnsw
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_event_seed(arguments: dict) -> str:
    """Return ranked context bundle for fabric_seed session warm-up (E-46 S-143)."""
    import asyncio

    projects = arguments.get("projects") or []
    if not projects:
        return json.dumps({"error": "projects required", "bundle": [], "degraded": True})

    goal = arguments.get("goal", "")
    top_k = int(arguments.get("top_k", 5))
    since_hours = float(arguments.get("since_hours", 24.0))

    try:
        store = _get_fabric_store()
        result = asyncio.run(
            store.fabric_seed_bundle(
                projects=projects,
                goal=goal,
                top_k=top_k,
                since_hours=since_hours,
            )
        )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc), "bundle": [], "degraded": True})


def _tool_agent_trail(arguments: dict) -> str:
    """Return AGENT_PUBLISHED + AGENT_RECEIVED EventEntities for an agent (E-46 S-143)."""
    from depthfusion.graph.store import get_store

    agent_id = arguments.get("agent_id", "")
    if not agent_id:
        return json.dumps({"error": "agent_id required", "trail": [], "count": 0})

    project_filter = arguments.get("project") or None
    since_str = arguments.get("since") or None
    until_str = arguments.get("until") or None

    since_ts: float | None = None
    until_ts: float | None = None
    try:
        if since_str:
            from datetime import datetime, timezone
            since_ts = datetime.fromisoformat(since_str).replace(tzinfo=timezone.utc).timestamp()
        if until_str:
            from datetime import datetime, timezone
            until_ts = datetime.fromisoformat(until_str).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError) as exc:
        return json.dumps({"error": f"invalid date format: {exc}", "trail": [], "count": 0})

    try:
        graph = get_store()
        all_entities = graph.all_entities()
        trail = []
        for entity in all_entities:
            if entity.type != "event":
                continue
            meta = entity.metadata or {}
            if meta.get("agent_id") != agent_id:
                continue
            if project_filter and meta.get("project_slug") != project_filter:
                continue
            try:
                from datetime import datetime, timezone
                ts = (
                    datetime.fromisoformat(entity.first_seen)
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
            except (ValueError, TypeError):
                ts = 0.0
            if since_ts is not None and ts < since_ts:
                continue
            if until_ts is not None and ts > until_ts:
                continue
            trail.append({
                "entity_id": entity.entity_id,
                "event_type": meta.get("event_type", ""),
                "memory_refs": meta.get("memory_refs", []),
                "first_seen": entity.first_seen,
                "project": meta.get("project_slug", entity.project),
                "session_id": meta.get("session_id"),
            })
        trail.sort(key=lambda x: x["first_seen"])
        return json.dumps({"trail": trail, "count": len(trail)})
    except Exception as exc:
        return json.dumps({"error": str(exc), "trail": [], "count": 0})


if __name__ == "__main__":
    main()
