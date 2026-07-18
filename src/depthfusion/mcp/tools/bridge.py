"""depthfusion MCP tool implementations — bridge domain."""
from __future__ import annotations

import json
import logging
from typing import Optional

try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

from depthfusion.core.config import DepthFusionConfig

logger = logging.getLogger("depthfusion.mcp.server")

from depthfusion.mcp.tools.capture import _tool_publish_context  # noqa: E402
from depthfusion.mcp.tools.recall import _tool_recall  # noqa: E402


def _tool_bridge(arguments: dict, config: Optional[DepthFusionConfig] = None) -> str:
    """Bridge to an OpenRouter LLM with memory injection.

    When *arguments* contains ``node_id`` (and optionally ``session_id``),
    the tool retrieves the raw offloaded text from refs/ and returns it
    directly — no LLM call is made.  This implements AC-3 of S-231.
    """
    import hashlib

    # S-231: node_id retrieval path — return raw offloaded text from refs/
    node_id = arguments.get("node_id", "")
    if node_id:
        session_id = str(arguments.get("session_id", ""))
        # Reject path-separator characters in caller-supplied identifiers
        # before they reach ContextOffloader._assert_confined().
        import re as _re
        _safe = _re.compile(r'^[A-Za-z0-9_\-]+$')
        if not _safe.match(node_id) or (session_id and not _safe.match(session_id)):
            return json.dumps({"error": "Invalid node_id or session_id", "node_id": node_id})
        try:
            from depthfusion.cognitive.offloader import ContextOffloader
            from depthfusion.core.config import DepthFusionConfig
            _cfg: DepthFusionConfig = config if config is not None else DepthFusionConfig()
            offloader = ContextOffloader(_cfg)
            raw_text = offloader.retrieve(node_id, session_id)
            return json.dumps({"node_id": node_id, "session_id": session_id, "text": raw_text})
        except (FileNotFoundError, PermissionError) as exc:
            return json.dumps({
                "error": str(exc),
                "node_id": node_id,
            })
        except Exception as exc:
            return json.dumps({"error": str(exc), "node_id": node_id})

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
        meta: dict = {"sub_scope": f"provider:{model}"}
        if context_tags:
            meta["context_tags"] = context_tags
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
            "metadata": meta,
        }
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

def register_bridge() -> None:
    """Register bridge domain tools (stub for v2 tooling framework)."""
    pass

