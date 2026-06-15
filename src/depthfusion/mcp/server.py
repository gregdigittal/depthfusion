"""DepthFusion MCP server — 21 tools, conditionally registered based on feature flags."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from typing import Any

from depthfusion.identity.models import Principal
from depthfusion.mcp.authz import AuthorizationError, check_tool_access

logger = logging.getLogger(__name__)

# Tool registry (TOOLS, _TOOL_FLAGS, TOOL_SCHEMAS, get_enabled_tools)
from depthfusion.mcp.tools._registry import (  # noqa: E402
    TOOL_SCHEMAS,
    TOOLS,
    _TOOL_FLAGS,
    get_enabled_tools,
)

# Recall implementation helpers (re-exported for test patching compatibility)
from depthfusion.mcp.tools._shared import (  # noqa: E402,F401
    _backend_name_to_chain,
    _detect_current_backends,
    _sanitise_project_slug,
    _split_into_blocks,
    _tool_recall_impl,
    _trim_to_sentence,
)

# Server state and infrastructure helpers
# Tool implementations — bridge domain
from depthfusion.mcp.tools.bridge import (  # noqa: E402,F401
    _tool_bridge,
)

# Tool implementations — capture domain
from depthfusion.mcp.tools.capture import (  # noqa: E402,F401
    _handle_ambient_capture,
    _tool_auto_learn,
    _tool_compress_session,
    _tool_ingest_conversation,
    _tool_inspect_discovery,
    _tool_prune_discoveries,
    _tool_publish_context,
    _tool_tag_session,
)

# Tool implementations — decisions domain
from depthfusion.mcp.tools.decisions import (  # noqa: E402,F401
    _tool_get_cognitive_state,
    _tool_mark_superseded,
    _tool_record_decision,
    _tool_record_incident,
    _tool_report_outcome,
    _tool_run_recursive,
)

# Tool implementations — graph domain
from depthfusion.mcp.tools.graph import (  # noqa: E402,F401
    _tool_agent_trail,
    _tool_confirm_discovery,
    _tool_event_publish,
    _tool_event_seed,
    _tool_graph_status,
    _tool_graph_traverse,
    _tool_pin_discovery,
    _tool_set_memory_score,
    _tool_set_scope,
)

# Tool implementations — project domain
from depthfusion.mcp.tools.project import (  # noqa: E402,F401
    _tool_ingest_project,
    _tool_list_projects,
    _tool_register_project,
    _tool_session_seed,
    _tool_sync_project,
)

# Tool implementations — recall domain
from depthfusion.mcp.tools.recall import (  # noqa: E402,F401
    _tool_recall,
    _tool_recall_feedback,
    _tool_retrieve_context,
)

# Tool implementations — system domain
from depthfusion.mcp.tools.system import (  # noqa: E402,F401
    _tool_list_providers,
    _tool_research_topic,
    _tool_status,
)

# Tool implementations — telemetry domain
from depthfusion.mcp.tools.telemetry import (  # noqa: E402,F401
    _check_backend_health,
    _emit_startup_event,
    _tool_describe_capabilities,
    _tool_hnsw_capability,
    _tool_query_telemetry,
    _tool_record_telemetry,
    _tool_surface_skill_candidates,
    _tool_tier_status,
)


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

def _handle_tools_call(
    tool_name: str,
    arguments: dict,
    config: Any,
    principal: Principal | None = None,
) -> dict:
    """Dispatch a tool call and return MCP-formatted result.

    Parameters
    ----------
    tool_name:
        The MCP tool identifier (e.g. ``"depthfusion_recall_relevant"``).
    arguments:
        Caller-supplied arguments.
    config:
        Server configuration.
    principal:
        The authenticated caller bound to this session.  ``None`` for
        unauthenticated stdio sessions (will be rejected by authz).
    """
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

    # Principal binding + capability check (T-578 / T-579)
    try:
        check_tool_access(tool_name, principal)
    except AuthorizationError as exc:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Authorization denied: {exc}"}],
        }

    # Dispatch to tool implementations
    try:
        result_text = _dispatch_tool(tool_name, arguments, config, principal)
        return {
            "isError": False,
            "content": [{"type": "text", "text": result_text}],
        }
    except Exception as exc:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Tool error: {exc}"}],
        }

def _dispatch_tool(
    tool_name: str,
    arguments: dict,
    config: Any,
    principal: Principal | None = None,
) -> str:
    """Route tool calls to their implementations.

    Parameters
    ----------
    tool_name:
        The MCP tool identifier.
    arguments:
        Caller-supplied arguments.
    config:
        Server configuration.
    principal:
        The authenticated caller.  By the time this function is called,
        ``check_tool_access`` has already validated the principal — callers
        that skip ``_handle_tools_call`` must call ``check_tool_access``
        themselves.
    """
    if tool_name == "depthfusion_status":
        return _tool_status(config)
    elif tool_name == "depthfusion_recall_relevant":
        return _tool_recall(arguments)
    elif tool_name == "depthfusion_tag_session":
        return _tool_tag_session(arguments)
    elif tool_name == "depthfusion_publish_context":
        return _tool_publish_context(arguments, config)
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
    elif tool_name == "depthfusion_recall_feedback":
        return _tool_recall_feedback(arguments)
    elif tool_name == "depthfusion_pin_discovery":
        return _tool_pin_discovery(arguments)
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
    elif tool_name == "depthfusion_record_telemetry":
        return _tool_record_telemetry(arguments, config)
    elif tool_name == "depthfusion_query_telemetry":
        return _tool_query_telemetry(arguments, config)
    elif tool_name == "depthfusion_session_seed":
        return _tool_session_seed(arguments)
    elif tool_name == "depthfusion_register_project":
        return _tool_register_project(arguments)
    elif tool_name == "depthfusion_list_projects":
        return _tool_list_projects(arguments)
    elif tool_name == "depthfusion_sync_project":
        return _tool_sync_project(arguments)
    elif tool_name == "depthfusion_ingest_project":
        return _tool_ingest_project(arguments)
    elif tool_name == "depthfusion_research_topic":
        return _tool_research_topic(arguments)
    elif tool_name == "depthfusion_bridge":
        return _tool_bridge(arguments)
    elif tool_name == "depthfusion_ingest_conversation":
        return _tool_ingest_conversation(arguments)
    elif tool_name == "depthfusion_list_providers":
        return _tool_list_providers()
    else:
        raise ValueError(f"No dispatcher for {tool_name}")

def _process_request(
    request: dict,
    config: Any,
    principal: Principal | None = None,
) -> dict:
    """Process a single JSON-RPC request and return the response.

    Parameters
    ----------
    request:
        Parsed JSON-RPC 2.0 request object.
    config:
        Server configuration.
    principal:
        The authenticated caller bound to this MCP session.  ``None`` for
        unauthenticated stdio sessions — tool calls will be rejected by the
        capability check unless the tool is accessible without a principal
        (currently none are).
    """
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "depthfusion", "version": "1.2.2"},
        }
    elif method == "tools/list":
        result = _handle_tools_list(config)
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = _handle_tools_call(tool_name, arguments, config, principal)
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

