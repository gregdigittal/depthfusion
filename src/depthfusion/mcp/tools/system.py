"""depthfusion MCP tool implementations — system domain."""
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

from depthfusion.mcp.tools._state import _get_hnsw_store, _get_context_bus, _get_fabric_store  # noqa: E402
from depthfusion.mcp.tools._registry import get_enabled_tools  # noqa: E402
from depthfusion.mcp.tools.capture import _tool_publish_context  # noqa: E402
from depthfusion.core.research import TopicResearcher  # noqa: E402


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

def _tool_list_providers() -> str:
    import json
    import os

    providers = []
    key = os.environ.get("OPENROUTER_API_KEY")
    backend = OpenRouterBackend() if key and OpenRouterBackend is not None else None
    providers.append({
        "name": "openrouter",
        "configured": bool(key),
        "healthy": backend.healthy() if backend else False,
        "memory_count": 0,
        "models": ["openai/gpt-4o", "google/gemini-1.5-pro", "deepseek/deepseek-chat"],
    })
    return json.dumps({"providers": providers})

def _tool_research_topic(arguments: dict) -> str:
    topic = arguments.get("topic", "").strip()
    slug = arguments.get("slug", "research").strip() or "research"
    sources = arguments.get("sources", ["web", "arxiv", "github"])
    if not topic:
        return json.dumps({"error": "topic is required"})
    if not isinstance(sources, list):
        sources = ["web", "arxiv", "github"]

    def _publish(slug: str, content: str, tags: list) -> None:
        _tool_publish_context({
            "item": {
                "item_id": f"research:{slug}:{tags[2] if len(tags) > 2 else topic}",
                "content": content,
                "source_agent": "depthfusion_research_topic",
                "tags": tags,
                "priority": "high",
            }
        })

    researcher = TopicResearcher(publish_fn=_publish)
    try:
        results = researcher.research(topic=topic, slug=slug, sources=sources)
        return json.dumps({
            "researched": True,
            "topic": topic,
            "saved_to": results.get("saved_to", ""),
            "source_counts": {k: len(v) for k, v in results["sources"].items()},
        })
    except Exception as e:
        return json.dumps({"error": str(e), "researched": False})

def register_system() -> None:
    """Register system domain tools (stub for v2 tooling framework)."""
    pass

