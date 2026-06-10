"""depthfusion MCP tool implementations — project domain."""
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
from depthfusion.mcp.tools.capture import _tool_publish_context  # noqa: E402

from depthfusion.core.project_registry import ProjectEntry as _ProjectEntry  # noqa: E402
from depthfusion.core.project_registry import ProjectRegistry  # noqa: E402
from depthfusion.core.project_context import sync_project as _sync_project_impl  # noqa: E402
from depthfusion.core.project_ingest import ProjectIngestor  # noqa: E402


def _tool_register_project(arguments: dict) -> str:
    slug = arguments.get("slug", "").strip()
    name = arguments.get("name", "").strip()
    local_path = arguments.get("local_path", "").strip()
    github_url = arguments.get("github_url", "").strip()
    description = arguments.get("description", "").strip()
    if not slug or not name or not local_path:
        return json.dumps({"error": "slug, name, and local_path are required"})
    if not Path(local_path).exists():
        return json.dumps({"error": f"local_path does not exist: {local_path}"})
    registry = ProjectRegistry()
    entry = registry.register(_ProjectEntry(
        slug=slug, name=name, local_path=local_path,
        github_url=github_url, description=description,
    ))
    return json.dumps({
        "registered": True, "slug": entry.slug,
        "name": entry.name, "local_path": entry.local_path,
    })

def _tool_list_projects(arguments: dict) -> str:
    registry = ProjectRegistry()
    projects = registry.list_projects()
    return json.dumps({
        "projects": [
            {
                "slug": p.slug, "name": p.name, "local_path": p.local_path,
                "github_url": p.github_url, "last_synced": p.last_synced,
                "description": p.description,
            }
            for p in projects
        ]
    })

def _tool_sync_project(arguments: dict) -> str:
    slug = arguments.get("slug", "").strip()
    if not slug:
        return json.dumps({"error": "slug is required"})
    try:
        from depthfusion.core.project_registry import ProjectRegistry
    except ImportError:
        return json.dumps({"error": "ProjectRegistry not available"})
    registry = ProjectRegistry()
    entry = registry.get(slug)
    if not entry:
        return json.dumps({
            "error": f"Project not registered: {slug}. Use depthfusion_register_project first."
        })

    def _publish(slug: str, content: str, tags: list) -> None:
        _tool_publish_context({
            "item": {
                "item_id": f"project_sync:{slug}:{tags[1] if len(tags) > 1 else 'context'}",
                "content": content,
                "source_agent": "depthfusion_sync_project",
                "tags": tags,
                "priority": "high",
            }
        })

    results = _sync_project_impl(slug=slug, local_path=entry.local_path, publish_fn=_publish)
    registry.update_last_synced(slug)
    return json.dumps({"synced": True, "slug": slug, "results": results})

def _tool_ingest_project(arguments: dict) -> str:
    slug = arguments.get("slug", "").strip()
    source = arguments.get("source", "").strip()
    mode = arguments.get("mode", "structural").strip()
    if not slug or not source:
        return json.dumps({"error": "slug and source are required"})
    if mode not in ("structural", "full"):
        return json.dumps({"error": "mode must be 'structural' or 'full'"})

    def _publish(slug: str, content: str, tags: list) -> None:
        import hashlib
        item_id = f"ingest:{slug}:{hashlib.md5(tags[-1].encode()).hexdigest()[:8]}"
        _tool_publish_context({
            "item": {
                "item_id": item_id,
                "content": content,
                "source_agent": "depthfusion_ingest_project",
                "tags": tags,
                "priority": "normal",
            }
        })

    ingestor = ProjectIngestor(publish_fn=_publish)
    try:
        is_github = (
            source.startswith('https://github.com')
            or source.startswith('http://github.com')
            or ('/' in source and not source.startswith('/') and not source.startswith('.'))
        )
        if is_github:
            result = ingestor.ingest_github(slug=slug, github_url=source, mode=mode)
        else:
            result = ingestor.ingest_local(slug=slug, local_path=source, mode=mode)
        return json.dumps({"ingested": True, "slug": slug, "result": result})
    except Exception as e:
        return json.dumps({"error": str(e), "ingested": False})

def _tool_session_seed(arguments: dict) -> str:
    """Publish top recall results as high-priority session-seed ContextItems (S-111/S-143)."""
    project_slug = arguments.get("project_slug", "").strip()
    project_context_prefix = ""
    if project_slug:
        try:
            from depthfusion.core.project_registry import ProjectRegistry
            _registry = ProjectRegistry()
            _entry = _registry.get(project_slug)
            if _entry:
                _ctx_parts = []
                from pathlib import Path as _Path
                _backlog = _Path(_entry.local_path) / "BACKLOG.md"
                if _backlog.exists():
                    _text = _backlog.read_text(encoding="utf-8")
                    # Extract active epics summary (first 3000 chars)
                    _ctx_parts.append(f"# Project: {project_slug}\n\n{_text[:3000]}")
                _claude_md = _Path(_entry.local_path) / "CLAUDE.md"
                if _claude_md.exists():
                    _ctx_parts.append(_claude_md.read_text(encoding="utf-8")[:2000])
                if _ctx_parts:
                    project_context_prefix = "\n\n---\n\n".join(_ctx_parts) + "\n\n---\n\n"
                else:
                    # AC-3: registered but no BACKLOG.md/CLAUDE.md to draw from
                    project_context_prefix = (
                        f"# Project context unavailable\n\n"
                        f"No project context found for {project_slug} — "
                        f"the project is registered but has no BACKLOG.md or CLAUDE.md. "
                        f"Run depthfusion_sync_project to refresh context.\n\n---\n\n"
                    )
            else:
                # AC-3: slug not registered — signal that registration is needed
                project_context_prefix = (
                    f"# Project context unavailable\n\n"
                    f"No project context found for {project_slug} — "
                    f"run depthfusion_sync_project to register\n\n---\n\n"
                )
        except Exception:
            pass  # project context is optional — don't break the seed

    import asyncio
    from pathlib import Path

    session_id = arguments.get("session_id", "unknown")
    mode = arguments.get("mode", "recall")

    if not session_id:
        result = {"error": "session_id required", "published": 0}
        if project_slug:
            result["project_slug"] = project_slug
        return json.dumps(result)

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
            if project_slug:
                result["project_slug"] = project_slug
                if project_context_prefix:
                    result["project_context_prefix"] = project_context_prefix
            return json.dumps(result)
        except Exception as exc:
            result = {
                "error": str(exc), "session_id": session_id, "bundle": [], "degraded": True,
            }
            if project_slug:
                result["project_slug"] = project_slug
                if project_context_prefix:
                    result["project_context_prefix"] = project_context_prefix
            return json.dumps(result)

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
        result = {
            "published": published,
            "query": query,
            "session_id": session_id,
        }
        if project_slug:
            result["project_slug"] = project_slug
            if project_context_prefix:
                result["project_context_prefix"] = project_context_prefix
        return json.dumps(result)
    except Exception as exc:
        result = {"error": str(exc), "published": 0, "session_id": session_id}
        if project_slug:
            result["project_slug"] = project_slug
            if project_context_prefix:
                result["project_context_prefix"] = project_context_prefix
        return json.dumps(result)

