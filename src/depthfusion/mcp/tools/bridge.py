"""depthfusion MCP tool implementations — bridge domain."""
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
try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

logger = logging.getLogger("depthfusion.mcp.server")

from depthfusion.mcp.tools._state import _get_hnsw_store, _get_context_bus, _get_fabric_store  # noqa: E402
from depthfusion.mcp.tools.recall import _tool_recall  # noqa: E402
from depthfusion.mcp.tools.capture import _tool_publish_context  # noqa: E402


def _tool_bridge(arguments: dict) -> str:
    import hashlib
    import json

    if OpenRouterBackend is None:
        return json.dumps({"error": "OPENROUTER_API_KEY not configured", "model": ""})

    model = str(arguments.get("model", "openai/gpt-4o"))
    prompt = str(arguments.get("prompt", ""))
    if not prompt:
        return json.dumps({"error": "prompt is required"})

    context_tags = arguments.get("context_tags") or []
    if isinstance(context_tags, str):
        context_tags = [context_tags]
    recall_args: dict = {"query": prompt, "top_k": 5}
    if context_tags:
        recall_args["sub_scope"] = context_tags[0] if len(context_tags) == 1 else context_tags

    try:
        recall_raw = _tool_recall(recall_args)
        recall_data = json.loads(recall_raw)
        blocks = recall_data.get("blocks", recall_data.get("results", []))
    except Exception:
        blocks = []

    memory_ctx = "\n\n".join(
        b.get("content", "") for b in blocks if b.get("content")
    )
    system = f"Relevant memory context:\n{memory_ctx}" if memory_ctx else None

    backend = OpenRouterBackend(model=model)
    if not backend.healthy():
        return json.dumps({"error": "OPENROUTER_API_KEY not configured", "model": model})

    try:
        response = backend.complete(prompt, max_tokens=2048, system=system, model=model)
    except Exception as exc:
        return json.dumps({"error": str(exc), "model": model})

    fragments_stored = 0
    try:
        item_payload = {
            "item_id": (
                "bridge:"
                + hashlib.sha256(
                    f"{model}:{prompt}:{response}".encode("utf-8")
                ).hexdigest()[:16]
            ),
            "content": f"[Bridge response from {model}]\nPrompt: {prompt}\n\nResponse: {response}",
            "source_agent": "depthfusion_bridge",
            "tags": ["bridge", f"provider:{model}"],
            "metadata": {"sub_scope": f"provider:{model}"},
        }
        if context_tags:
            item_payload["metadata"]["context_tags"] = context_tags
        publish_args = {"item": item_payload}
        _tool_publish_context(publish_args)
        fragments_stored = 1
    except Exception:
        pass

    return json.dumps({
        "response": response,
        "model": model,
        "memories_injected": len(blocks),
        "fragments_stored": fragments_stored,
    })

