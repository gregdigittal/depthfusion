"""depthfusion MCP tool implementations — project domain."""
from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

logger = logging.getLogger("depthfusion.mcp.server")

from depthfusion.core.project_context import sync_project as _sync_project_impl  # noqa: E402
from depthfusion.core.project_ingest import ProjectIngestor  # noqa: E402
from depthfusion.core.project_registry import ProjectEntry as _ProjectEntry  # noqa: E402
from depthfusion.core.project_registry import ProjectRegistry  # noqa: E402
from depthfusion.mcp.tools._state import _get_fabric_store  # noqa: E402
from depthfusion.mcp.tools.capture import _tool_publish_context  # noqa: E402


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

def _tool_session_seed_resume(
    *,
    session_id: str,
    project_slug: str,
    checkpoint_id: str | None = None,
    top_k: int = 3,
    snippet_len: int = 800,
    project_context_prefix: str = "",
) -> str:
    """`depthfusion_session_seed(mode="resume")` — S-250 AC-3.

    Returns the named checkpoint (``checkpoint_id``) or the most recent one
    for ``project_slug`` when omitted, alongside standard recall results
    (S-111's ``_recall_and_seed``), plus a ``recovery_command`` when the
    checkpoint carries a ``git_stash_ref``.
    """
    if not project_slug:
        return json.dumps({
            "error": "project_slug required for resume mode",
            "session_id": session_id,
            "checkpoint": None,
        })

    from depthfusion.hooks.session_start import (
        _build_seed_query,
        _detect_project_name,
        _recall_and_seed,
        _recent_git_messages,
    )

    try:
        store = _get_fabric_store()
        checkpoint = (
            store.get_checkpoint(checkpoint_id, project_slug)
            if checkpoint_id
            else store.get_latest_checkpoint(project_slug)
        )
        if checkpoint is None:
            result: dict = {
                "error": (
                    f"No checkpoint found for project {project_slug!r}"
                    + (f" with checkpoint_id={checkpoint_id!r}" if checkpoint_id else "")
                ),
                "checkpoint": None,
                "session_id": session_id,
                "project_slug": project_slug,
            }
            if project_context_prefix:
                result["project_context_prefix"] = project_context_prefix
            return json.dumps(result)

        cwd = Path.cwd()
        project_name = _detect_project_name(cwd)
        git_messages = _recent_git_messages(cwd)
        query = _build_seed_query(project_name, git_messages)
        published = _recall_and_seed(session_id, top_k=top_k, snippet_len=snippet_len)

        result = {
            "checkpoint": checkpoint.to_dict(),
            "published": published,
            "query": query,
            "session_id": session_id,
            "project_slug": project_slug,
        }
        if checkpoint.git_stash_ref:
            result["recovery_command"] = f"git stash pop {checkpoint.git_stash_ref}"
        if project_context_prefix:
            result["project_context_prefix"] = project_context_prefix
        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001 — resume must degrade, not crash the tool call
        logger.warning("session_seed resume mode failed for project=%s: %s", project_slug, exc)
        result = {
            "error": str(exc),
            "checkpoint": None,
            "session_id": session_id,
            "project_slug": project_slug,
        }
        if project_context_prefix:
            result["project_context_prefix"] = project_context_prefix
        return json.dumps(result)


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

    if mode == "resume":
        return _tool_session_seed_resume(
            session_id=session_id,
            project_slug=project_slug,
            checkpoint_id=arguments.get("checkpoint_id") or None,
            top_k=int(arguments.get("top_k", 3)),
            snippet_len=int(arguments.get("snippet_len", 800)),
            project_context_prefix=project_context_prefix,
        )

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

def _tool_session_checkpoint(arguments: dict) -> str:
    """Publish a manual ``CheckpointRecord`` at a plan boundary (E-73 T-850).

    Thin wrapper over ``EventStore.publish_checkpoint`` — reuses the existing
    schema and persistence path (JSON file + best-effort graph mirror) rather
    than duplicating publish logic. The resulting checkpoint is retrievable
    via ``EventStore.get_checkpoint`` / ``get_latest_checkpoint``, and by
    ``depthfusion_session_seed(mode="resume")``.

    Args: session_id (str, required), project_slug (str, required),
    plan_state (str, required), files_modified (list[str], required),
    git_stash_ref (str, optional), context_pct_at_checkpoint (number, optional).
    Response: {checkpoint_id, created_at, session_id, project_slug} or {error}.
    """
    import asyncio

    session_id = str(arguments.get("session_id", "")).strip()
    project_slug = str(arguments.get("project_slug", "")).strip()
    plan_state = arguments.get("plan_state", "")
    files_modified = arguments.get("files_modified")

    if not session_id:
        return json.dumps({"error": "session_id is required"})
    if not project_slug:
        return json.dumps({"error": "project_slug is required"})
    if not isinstance(plan_state, str) or not plan_state.strip():
        return json.dumps({"error": "plan_state is required"})
    if not isinstance(files_modified, list):
        return json.dumps({"error": "files_modified is required and must be a list"})

    git_stash_ref = arguments.get("git_stash_ref") or None
    context_pct_raw = arguments.get("context_pct_at_checkpoint")
    if context_pct_raw is None:
        context_pct_at_checkpoint = None
    else:
        try:
            context_pct_at_checkpoint = float(context_pct_raw)
        except (TypeError, ValueError):
            return json.dumps({
                "error": "context_pct_at_checkpoint must be a number",
            })

    try:
        from depthfusion.core.event_store import project_root_path

        store = _get_fabric_store()
        record = asyncio.run(
            store.publish_checkpoint(
                session_id=session_id,
                project_slug=project_slug,
                plan_state=plan_state,
                files_modified=[str(f) for f in files_modified],
                git_stash_ref=git_stash_ref,
                context_pct_at_checkpoint=context_pct_at_checkpoint,
                # No working-tree path is in scope for a stdio tool call, so
                # the git root comes from DEPTHFUSION_PROJECT_PATH (default
                # os.getcwd()) — see project_root_path() (T-863).
                project_path=project_root_path(),
            )
        )
        return json.dumps({
            "checkpoint_id": record.checkpoint_id,
            "created_at": record.created_at,
            "session_id": record.session_id,
            "project_slug": record.project_slug,
        })
    except Exception as exc:  # noqa: BLE001 — tool call must degrade, not crash
        logger.warning(
            "session_checkpoint failed for project=%s session=%s: %s",
            project_slug, session_id, exc,
        )
        return json.dumps({"error": str(exc)})


def register_project() -> None:
    """Register project domain tools (stub for v2 tooling framework)."""
    pass

