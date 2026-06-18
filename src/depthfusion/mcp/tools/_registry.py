"""depthfusion MCP tool registry — TOOLS, _TOOL_FLAGS, TOOL_SCHEMAS dicts."""
from __future__ import annotations

from typing import Any

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
        "{bundle: [...], degraded: bool, session_id: str} for fabric_seed. "
        "Optional: project_slug (str) — include project backlog and context in the seed output."
    ),
    # S-147 project registry tools
    "depthfusion_register_project": (
        "Register a local project with DepthFusion for context tracking. "
        "Args: slug (str, required) — short identifier e.g. 'depthfusion'; "
        "name (str, required) — human-readable name; "
        "local_path (str, required) — absolute path on this host; "
        "github_url (str, optional); description (str, optional). "
        "Response: {registered: bool, slug, name, local_path}"
    ),
    "depthfusion_list_projects": (
        "List all registered projects. No args required. "
        "Response: {projects: [{slug, name, local_path, github_url, last_synced, description}]}"
    ),
    "depthfusion_sync_project": (
        "Sync a registered project's BACKLOG.md, CLAUDE.md, and recent git log "
        "into the DepthFusion knowledge base. Args: slug (str, required). "
        "Response: {synced: bool, slug, results: {backlog?, claude_md?, git_log?}}"
    ),
    "depthfusion_ingest_project": (
        "Ingest a project into the DepthFusion knowledge base. "
        "Supports local paths and GitHub URLs. "
        "Args: slug (str, required); source (str, required) — absolute local path OR GitHub URL "
        "(e.g. https://github.com/owner/repo or owner/repo); "
        "mode (str, optional, default 'structural') — 'structural' ingests key files only, "
        "'full' ingests all source files. "
        "Response: {ingested: bool, slug, result: {files_ingested, bytes_ingested?, mode, source}}"
    ),
    "depthfusion_research_topic": (
        "Research a topic using web search (DuckDuckGo), arXiv, and GitHub. "
        "Results are stored in ~/.claude/shared/research/ and published to the DepthFusion KB. "
        "Args: topic (str, required); slug (str, optional, default 'research') — tag prefix; "
        "sources (list[str], optional, default ['web','arxiv','github']). "
        "Response: {researched: bool, topic, saved_to, source_counts: {web, arxiv, github}}"
    ),
    "depthfusion_bridge": (
        "Delegate a prompt to an external LLM via OpenRouter with shared DepthFusion memory. "
        "Recalls relevant context, sends to provider, stores response fragments. "
        "Args: model (str, required) — OpenRouter model string e.g. openai/gpt-4o, "
        "google/gemini-1.5-pro, deepseek/deepseek-chat; "
        "prompt (str, required); context_tags (list[str], optional) — sub_scope filter. "
        "Response: {response, model, memories_injected, fragments_stored}"
    ),
    "depthfusion_ingest_conversation": (
        "Bulk-import a past conversation from ChatGPT, Gemini, or DeepSeek into DepthFusion memory. "  # noqa: E501
        "Args: provider (str, required) — chatgpt|gemini|deepseek|generic; "
        "data (str, required) — raw conversation export JSON or text. "
        "Response: {fragments_stored, skipped, provider, errors}"
    ),
    "depthfusion_list_providers": (
        "List configured bridge providers and their status. "
        "Shows which providers are ready for depthfusion_bridge calls. "
        "No arguments required. "
        "Response: {providers: [{name, configured, healthy, memory_count}]}"
    ),
}

# Map tools to the feature flags that gate them
_TOOL_FLAGS: dict[str, str | None] = {
    "depthfusion_status": None,               # always enabled
    "depthfusion_recall_relevant": None,       # always enabled
    "depthfusion_tag_session": None,           # always enabled
    "depthfusion_publish_context": "router_enabled",
    "depthfusion_auto_learn": None,
    "depthfusion_compress_session": None,
    "depthfusion_graph_traverse": "graph_enabled",
    "depthfusion_graph_status": "graph_enabled",
    "depthfusion_set_scope": "graph_enabled",
    "depthfusion_confirm_discovery": None,          # always enabled (CM-5)
    "depthfusion_set_memory_score": None,           # always enabled (S-70)
    "depthfusion_recall_feedback": None,          # always enabled (S-72)
    "depthfusion_pin_discovery": None,            # always enabled (S-69)
    # E-31 cognitive tools
    "depthfusion_retrieve_context": "cognitive_retrieval",
    "depthfusion_record_decision": "decision_memory",
    "depthfusion_record_incident": "operational_memory",
    "depthfusion_mark_superseded": "operational_memory",
    "depthfusion_report_outcome": "operational_memory",
    # E-33 telemetry tools
    "depthfusion_record_telemetry": None,         # always enabled (E-33 S-106)
    "depthfusion_query_telemetry": None,          # always enabled (E-33 S-106)
    # E-35 S-111 session-start auto-recall seed
    "depthfusion_session_seed": None,             # always enabled (E-35 S-111)
    # S-147 project registry tools
    "depthfusion_register_project": None,
    "depthfusion_list_projects": None,
    # S-148 BACKLOG sync pipeline
    "depthfusion_sync_project": None,
    # S-150 project ingest
    "depthfusion_ingest_project": None,
    # S-152 topic research
    "depthfusion_research_topic": None,
    "depthfusion_bridge": None,
    "depthfusion_ingest_conversation": None,
    "depthfusion_list_providers": None,
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
            "project_slug": {
                "type": "string",
                "description": "Project slug used to include project backlog and context in seed",
            },
        },
        "required": ["session_id"],
    },
    "depthfusion_bridge": {
        "properties": {
            "model": {"type": "string", "description": "OpenRouter model string e.g. openai/gpt-4o"},  # noqa: E501
            "prompt": {"type": "string", "description": "The prompt to send to the provider"},
            "context_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional sub_scope tags to filter recalled memories",
            },
        },
        "required": ["model", "prompt"],
    },
    "depthfusion_ingest_conversation": {
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["chatgpt", "gemini", "deepseek", "generic"],
                "description": "Conversation export format",
            },
            "data": {
                "type": "string",
                "description": "Raw conversation export JSON or text",
            },
        },
        "required": ["provider", "data"],
    },
    "depthfusion_list_providers": {
        "properties": {},
        "required": [],
    },
}


