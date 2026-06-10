"""DepthFusion MCP tool domain modules.

Each sub-module owns one domain of tool implementations.
"""
from depthfusion.mcp.tools.recall import register_recall
from depthfusion.mcp.tools.capture import register_capture
from depthfusion.mcp.tools.bridge import register_bridge
from depthfusion.mcp.tools.decisions import register_decisions
from depthfusion.mcp.tools.graph import register_graph
from depthfusion.mcp.tools.project import register_project
from depthfusion.mcp.tools.system import register_system
from depthfusion.mcp.tools.telemetry import register_telemetry

__all__ = [
    "register_recall",
    "register_capture",
    "register_bridge",
    "register_decisions",
    "register_graph",
    "register_project",
    "register_system",
    "register_telemetry",
]
