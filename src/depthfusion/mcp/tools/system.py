"""depthfusion MCP tool implementations — system domain."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

logger = logging.getLogger("depthfusion.mcp.server")

from depthfusion.core.research import TopicResearcher  # noqa: E402
from depthfusion.mcp.tools._registry import get_enabled_tools  # noqa: E402
from depthfusion.mcp.tools.capture import _tool_publish_context  # noqa: E402


def _tool_status(config: Any) -> str:
    import dataclasses

    enabled = get_enabled_tools(config)

    # Reflect all boolean fields from DepthFusionConfig, grouped by default.
    on_by_default: dict[str, bool] = {}
    behind_flag: dict[str, bool] = {}
    backends: dict[str, str] = {}

    _BACKEND_FIELDS = {
        "reranker_backend", "extractor_backend", "linker_backend",
        "summariser_backend", "embedding_backend", "decision_extractor_backend",
        "bus_backend",
    }
    _SKIP_FIELDS = {
        "ambient_skip_tools", "skillforge_api_url", "skillforge_api_token",
        "skillforge_recursive_skill_id", "bus_file_dir", "api_token",
        "mcp_http_token", "gemma_url", "gemma_model", "event_log",
        "gemma_model",
    }

    try:
        for f in dataclasses.fields(config):
            name = f.name
            if name in _SKIP_FIELDS:
                continue
            val = getattr(config, name, None)
            if name in _BACKEND_FIELDS:
                backends[name] = val or ""
            elif isinstance(val, bool):
                default = f.default if f.default is not dataclasses.MISSING else None
                if default is True:
                    on_by_default[name] = val
                else:
                    behind_flag[name] = val
    except Exception:
        pass

    return json.dumps(
        {
            "depthfusion": "active",
            # Back-compat top-level keys preserved
            "enabled_tools": enabled,
            "rlm_enabled": getattr(config, "rlm_enabled", True),
            "router_enabled": getattr(config, "router_enabled", True),
            "session_enabled": getattr(config, "session_enabled", True),
            "fusion_enabled": getattr(config, "fusion_enabled", True),
            # Full config reflection (S-221)
            "effective_flags": {
                "on_by_default": on_by_default,
                "behind_flag": behind_flag,
                "backends": backends,
            },
        },
        indent=2,
    )

def _tool_list_providers() -> str:
    import json

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

