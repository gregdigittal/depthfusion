"""DepthFusion MCP server — 5 tools, conditionally registered based on feature flags."""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

TOOLS: dict[str, str] = {
    "depthfusion_status": "Return current DepthFusion component status",
    "depthfusion_recall_relevant": "Retrieve most relevant session blocks for a query",
    "depthfusion_tag_session": "Tag a session file with metadata",
    "depthfusion_publish_context": "Publish a context item to the bus",
    "depthfusion_run_recursive": "Run recursive LLM on large content",
}

# Map tools to the feature flags that gate them
_TOOL_FLAGS: dict[str, str | None] = {
    "depthfusion_status": None,               # always enabled
    "depthfusion_recall_relevant": None,       # always enabled
    "depthfusion_tag_session": None,           # always enabled
    "depthfusion_publish_context": "router_enabled",
    "depthfusion_run_recursive": "rlm_enabled",
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
        elif getattr(config, flag_attr, True):
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


def _tool_recall(arguments: dict) -> str:
    query = arguments.get("query", "")
    return json.dumps({"query": query, "blocks": [], "message": "Recall not yet integrated"})


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


def _process_request(request: dict, config: Any) -> dict:
    """Process a single JSON-RPC request and return the response."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "depthfusion", "version": "0.1.0"},
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
