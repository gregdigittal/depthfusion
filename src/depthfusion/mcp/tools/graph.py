"""depthfusion MCP tool implementations — graph domain."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

from depthfusion.mcp.tools._shared import _sanitise_project_slug
from depthfusion.mcp.tools._state import _get_fabric_store, _get_hnsw_store

logger = logging.getLogger("depthfusion.mcp.server")


def _tool_graph_traverse(arguments: dict) -> str:
    """Traverse entity graph from a named entity."""
    graph_enabled = os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true"
    if not graph_enabled:
        return json.dumps({"error": "DEPTHFUSION_GRAPH_ENABLED is not set"})

    from depthfusion.graph.store import get_store
    from depthfusion.graph.traverser import traverse

    entity_name = arguments.get("entity_name", "")
    depth = min(int(arguments.get("depth", 1)), 3)
    relationship_filter = arguments.get("relationship_filter") or None

    store = get_store()
    all_entities = store.all_entities()
    match = next(
        (e for e in all_entities if e.name.lower() == entity_name.lower()), None
    )
    if not match:
        return json.dumps({
            "error": f"Entity not found: {entity_name}",
            "available": [e.name for e in all_entities[:20]],
        })

    result = traverse(match.entity_id, store, depth=depth, relationship_filter=relationship_filter)
    if not result:
        return json.dumps({"error": "Traversal failed"})

    return json.dumps({
        "origin": {
            "name": result.origin_entity.name,
            "type": result.origin_entity.type,
            "confidence": result.origin_entity.confidence,
        },
        "connected": [
            {
                "name": e.name, "type": e.type, "relationship": edge.relationship,
                "weight": edge.weight, "signals": edge.signals,
            }
            for e, edge in result.connected
        ],
        "depth": result.depth,
    }, indent=2)

def _tool_graph_status() -> str:
    """Report graph health and coverage."""
    graph_enabled = os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true"
    if not graph_enabled:
        return json.dumps({
            "graph_enabled": False,
            "message": "Set DEPTHFUSION_GRAPH_ENABLED=true to activate",
        })

    from depthfusion.graph.store import get_store
    store = get_store()
    entities = store.all_entities()
    type_breakdown: dict[str, int] = {}
    for e in entities:
        type_breakdown[e.type] = type_breakdown.get(e.type, 0) + 1

    haiku_enabled = os.environ.get("DEPTHFUSION_HAIKU_ENABLED", "false").lower() == "true"
    return json.dumps({
        "graph_enabled": True,
        "node_count": store.node_count(),
        "edge_count": store.edge_count(),
        "entities_by_type": type_breakdown,
        "tier": os.environ.get("DEPTHFUSION_MODE", "local"),
        "extraction_active": haiku_enabled,   # S-74: graph extraction requires HAIKU_ENABLED
        "tier_gates_extraction": False,        # no tier gate — runs on any tier when flags set
    }, indent=2)

def _tool_confirm_discovery(arguments: dict) -> str:
    """CM-5: Actively confirm a decision or fact for immediate capture.

    Writes a discovery file tagged `type: decisions` immediately — no LLM call
    required. Claude can call this during a session to capture an architectural
    decision, confirmed value, or established pattern the moment it is resolved.

    Arguments:
        text     (str, required): The decision or fact to capture (≤ 300 chars)
        project  (str, optional): Project slug (auto-detected from cwd if absent)
        category (str, optional): one of decision|fact|pattern|error_fix|value
                                   (default: "decision")
        confidence (float, optional): 0.0–1.0 (default: 0.95 — user confirmed)
    """
    text = str(arguments.get("text", "")).strip()
    if not text:
        return json.dumps({
            "ok": False,
            "error": "text argument is required",
        })
    if len(text) > 300:
        text = text[:300]

    # Sanitise any externally-supplied slug against path traversal before it
    # reaches write_decisions() (which uses the slug as a filename component).
    project = _sanitise_project_slug(str(arguments.get("project", "")))
    if not project:
        # Auto-detect from git remote or cwd; guard the import so a broken
        # git_post_commit module can't take down the confirmation tool.
        try:
            from depthfusion.hooks.git_post_commit import detect_project
            project = detect_project()
        except Exception:
            project = "unknown"

    category = str(arguments.get("category", "decision")).strip()
    if category not in ("decision", "fact", "pattern", "error_fix", "value"):
        category = "decision"

    confidence = float(arguments.get("confidence", 0.95))
    confidence = max(0.0, min(1.0, confidence))


    from depthfusion.capture.decision_extractor import DecisionEntry, write_decisions

    entry = DecisionEntry(
        text=text,
        confidence=confidence,
        category=category,
        source_session="mcp_confirm",
    )

    try:
        # S-60 / T-190: the metrics bucket for this path is
        # "confirm_discovery" (the high-level MCP tool), not
        # "decision_extractor" (the underlying writer). The override
        # kwarg on write_decisions threads the mechanism name through.
        out = write_decisions(
            [entry],
            project=project,
            session_id="mcp_confirm",
            capture_mechanism="confirm_discovery",
        )
        if out:
            return json.dumps({
                "ok": True,
                "written": str(out),
                "project": project,
                "text": text,
                "category": category,
                "confidence": confidence,
            }, indent=2)
        # File already exists for today — still succeeds, just idempotent
        return json.dumps({
            "ok": True,
            "written": None,
            "note": "Discovery file for today already exists; entry not appended "
                    "(use a new session or delete the file to re-capture)",
            "project": project,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})

def _tool_set_memory_score(arguments: dict) -> str:
    """E-27 / S-70 — operator override of importance/salience on a discovery.

    Atomic, lock-serialized read-modify-write:
      1. Acquire ``fcntl.LOCK_EX`` on a sidecar lock file
         (``<target>.scorelock``). Holds for the entire RMW critical
         section so two concurrent partial updates (caller A supplies
         only ``importance``, caller B supplies only ``salience``) cannot
         each read a stale version and silently overwrite the other's
         change. Mirrors the S-78 ``FileBus`` flock pattern.
      2. Re-read the file under lock; parse existing scoring frontmatter.
      3. For each unsupplied field, preserve the file's current value;
         for each supplied field, validate / clamp via ``MemoryScore``.
      4. Splice the new scalars into the frontmatter block.
      5. Write to a unique ``mkstemp`` sibling, ``fsync``, then
         ``os.replace`` over the target. ``os.replace`` is atomic on
         POSIX — a kill mid-write leaves the previous file intact.
      6. Release the lock.

    Idempotent: replaying the same payload produces byte-identical file
    content (provided no concurrent writer ran between calls). Out-of-
    range values are clamped via ``MemoryScore.__post_init__``.
    """
    filename = arguments.get("filename")
    importance = arguments.get("importance")
    salience = arguments.get("salience")

    if not isinstance(filename, str) or not filename.strip():
        return json.dumps({
            "ok": False,
            "error": "set_memory_score: 'filename' must be a non-empty string",
        })
    if importance is None and salience is None:
        return json.dumps({
            "ok": False,
            "error": "set_memory_score: at least one of 'importance' or "
                     "'salience' must be supplied",
        })

    # Type-validate scalar inputs at the boundary so a JSON-RPC client
    # passing strings (e.g. `"0.88"`) gets the documented error shape
    # rather than a TypeError bubbling out of MemoryScore.__post_init__.
    for fname, fval in (("importance", importance), ("salience", salience)):
        if fval is not None and not isinstance(fval, (int, float)):
            return json.dumps({
                "ok": False,
                "error": f"set_memory_score: '{fname}' must be a number, "
                         f"got {type(fval).__name__}",
            })

    target = Path(filename).expanduser().resolve()
    # ponytail: path confinement — external callers (e.g. ChatGPT MCP) must not
    # write to arbitrary server paths; discoveries dir is the only allowed root.
    # DEPTHFUSION_DISCOVERIES_DIR overrides for testing.
    _override = os.environ.get("DEPTHFUSION_DISCOVERIES_DIR")
    _allowed = Path(_override).resolve() if _override else (Path.home() / ".claude" / "shared" / "discoveries").resolve()
    if not str(target).startswith(str(_allowed) + os.sep) and target != _allowed:
        return json.dumps({
            "ok": False,
            "error": f"set_memory_score: path outside allowed directory: {filename}",
        })
    if not target.exists():
        return json.dumps({
            "ok": False,
            "error": f"set_memory_score: file not found: {filename}",
        })
    if not target.is_file():
        return json.dumps({
            "ok": False,
            "error": f"set_memory_score: not a regular file: {filename}",
        })

    from depthfusion.capture.dedup import extract_memory_score
    from depthfusion.core.file_locking import atomic_frontmatter_rewrite
    from depthfusion.core.types import MemoryScore

    try:
        with atomic_frontmatter_rewrite(target) as ctx:
            existing = extract_memory_score(ctx.body)
            final_imp = existing.importance if importance is None else importance
            final_sal = existing.salience if salience is None else salience
            normalized = MemoryScore(importance=final_imp, salience=final_sal)
            ctx.set_score(
                importance=normalized.importance,
                salience=normalized.salience,
            )
    except FileNotFoundError:
        return json.dumps({
            "ok": False, "error": f"set_memory_score: file not found: {filename}",
        })
    except OSError as exc:
        return json.dumps({
            "ok": False, "error": f"set_memory_score: write failed: {exc}",
        })
    except Exception as exc:  # noqa: BLE001 — MCP tool boundary; must not raise
        return json.dumps({
            "ok": False, "error": f"set_memory_score: unexpected error: {exc}",
        })

    return json.dumps({
        "ok": True,
        "filename": str(target),
        "importance": normalized.importance,
        "salience": normalized.salience,
    })

def _tool_set_scope(arguments: dict) -> str:
    """Programmatically set session graph scope."""
    from datetime import datetime, timezone

    from depthfusion.graph.scope import write_scope
    from depthfusion.graph.types import GraphScope

    # Read `scope` (schema-advertised key); fall back to `mode` for back-compat.
    mode = arguments.get("scope") or arguments.get("mode") or "project"
    projects = arguments.get("projects") or []

    if mode not in ("project", "cross_project", "global"):
        return json.dumps({"error": f"Invalid scope: {mode}. Use project|cross_project|global"})

    # ADR-001: sub_scope is ORTHOGONAL to mode — never cleared by mode value.
    sub_scope_raw = arguments.get("sub_scope")
    sub_scope: str | None = None
    if sub_scope_raw is not None:
        s = str(sub_scope_raw).strip()
        sub_scope = s or None  # empty/whitespace -> None (Room filtering off)

    scope = GraphScope(
        mode=mode,
        active_projects=projects,
        session_id="mcp_set",
        set_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        sub_scope=sub_scope,  # NEW — ADR-001
    )
    write_scope(scope)
    return json.dumps({
        "ok": True,
        "mode": mode,
        "active_projects": projects,
        "sub_scope": sub_scope,  # NEW — echo resolved value (None if off)
    })

def _tool_pin_discovery(arguments: dict) -> str:
    """S-69 — pin or unpin a discovery file to exempt it from age-based pruning.

    Atomic, lock-serialized read-modify-write (same pattern as
    ``_tool_set_memory_score``):
      1. Validate ``filename`` and ``pinned`` arguments.
      2. Resolve the path; return a structured error if not found.
      3. Acquire the sidecar lock and splice ``pinned: true/false`` into
         the YAML frontmatter via ``_splice_pin_frontmatter``.
      4. Write via mkstemp + os.replace.

    Idempotent: calling pin twice or unpin twice produces the same result.
    Missing file: returns ``{"error": "file not found", "filename": str}``
    (does NOT raise).
    """
    filename = arguments.get("filename")
    pinned_raw = arguments.get("pinned", True)

    if not isinstance(filename, str) or not filename.strip():
        return json.dumps({
            "error": "pin_discovery: 'filename' must be a non-empty string",
            "filename": filename,
        })
    if not isinstance(pinned_raw, bool):
        return json.dumps({
            "error": (
                f"pin_discovery: 'pinned' must be a bool, "
                f"got {type(pinned_raw).__name__}"
            ),
            "filename": filename,
        })

    target = Path(filename).expanduser().resolve()
    # ponytail: path confinement — same guard as set_memory_score
    # DEPTHFUSION_DISCOVERIES_DIR overrides for testing.
    _override = os.environ.get("DEPTHFUSION_DISCOVERIES_DIR")
    _allowed = Path(_override).resolve() if _override else (Path.home() / ".claude" / "shared" / "discoveries").resolve()
    if not str(target).startswith(str(_allowed) + os.sep) and target != _allowed:
        return json.dumps({
            "error": "path outside allowed directory",
            "filename": filename,
        })
    if not target.exists():
        return json.dumps({
            "error": "file not found",
            "filename": filename,
        })
    if not target.is_file():
        return json.dumps({
            "error": "not a regular file",
            "filename": filename,
        })

    from depthfusion.core.file_locking import atomic_frontmatter_rewrite

    try:
        with atomic_frontmatter_rewrite(target) as ctx:
            ctx.set_pinned(pinned_raw)
    except FileNotFoundError:
        return json.dumps({
            "error": "file not found",
            "filename": filename,
        })
    except OSError as exc:
        return json.dumps({
            "error": f"write failed: {exc}",
            "filename": filename,
        })
    except Exception as exc:  # noqa: BLE001 — MCP tool boundary; must not raise
        return json.dumps({
            "error": f"unexpected error: {exc}",
            "filename": filename,
        })

    return json.dumps({
        "pinned": pinned_raw,
        "filename": str(target),
    })

def _tool_event_publish(arguments: dict) -> str:
    """Publish content as a MemoryEntity + EventEntity with content-hash dedup (E-46 S-143)."""
    import asyncio

    content = arguments.get("content", "")
    agent_id = arguments.get("agent_id", "")
    project_slug = arguments.get("project_slug", "")

    if not content:
        return json.dumps({"error": "content required"})
    if not agent_id:
        return json.dumps({"error": "agent_id required"})
    if not project_slug:
        return json.dumps({"error": "project_slug required"})

    session_id = arguments.get("session_id") or None
    event_type = arguments.get("event_type", "publish")

    try:
        store = _get_fabric_store()
        result = asyncio.run(
            store.publish_memory(
                content=content,
                agent_id=agent_id,
                project_slug=project_slug,
                event_type=event_type,
                session_id=session_id,
            )
        )
        indexed_in_hnsw = False
        if not result.get("deduped"):
            hnsw = _get_hnsw_store()
            if hnsw is not None:
                try:
                    hnsw.upsert(entity_id=result["memory_id"], text=content, project=project_slug)
                    indexed_in_hnsw = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[event_publish] HNSW upsert failed (non-fatal): %s", exc)
        result["indexed_in_hnsw"] = indexed_in_hnsw
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

def _tool_event_seed(arguments: dict) -> str:
    """Return ranked context bundle for fabric_seed session warm-up (E-46 S-143)."""
    import asyncio

    projects = arguments.get("projects") or []
    if not projects:
        return json.dumps({"error": "projects required", "bundle": [], "degraded": True})

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
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc), "bundle": [], "degraded": True})

def _tool_agent_trail(arguments: dict) -> str:
    """Return AGENT_PUBLISHED + AGENT_RECEIVED EventEntities for an agent (E-46 S-143)."""
    from depthfusion.graph.store import get_store

    agent_id = arguments.get("agent_id", "")
    if not agent_id:
        return json.dumps({"error": "agent_id required", "trail": [], "count": 0})

    project_filter = arguments.get("project") or None
    since_str = arguments.get("since") or None
    until_str = arguments.get("until") or None

    since_ts: float | None = None
    until_ts: float | None = None
    try:
        if since_str:
            from datetime import datetime, timezone
            since_ts = datetime.fromisoformat(since_str).replace(tzinfo=timezone.utc).timestamp()
        if until_str:
            from datetime import datetime, timezone
            until_ts = datetime.fromisoformat(until_str).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError) as exc:
        return json.dumps({"error": f"invalid date format: {exc}", "trail": [], "count": 0})

    try:
        graph = get_store()
        all_entities = graph.all_entities()
        trail = []
        for entity in all_entities:
            if entity.type != "event":
                continue
            meta = entity.metadata or {}
            if meta.get("agent_id") != agent_id:
                continue
            if project_filter and meta.get("project_slug") != project_filter:
                continue
            try:
                from datetime import datetime, timezone
                ts = (
                    datetime.fromisoformat(entity.first_seen)
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
            except (ValueError, TypeError):
                ts = 0.0
            if since_ts is not None and ts < since_ts:
                continue
            if until_ts is not None and ts > until_ts:
                continue
            trail.append({
                "entity_id": entity.entity_id,
                "event_type": meta.get("event_type", ""),
                "memory_refs": meta.get("memory_refs", []),
                "first_seen": entity.first_seen,
                "project": meta.get("project_slug", entity.project),
                "session_id": meta.get("session_id"),
            })
        trail.sort(key=lambda x: x["first_seen"])
        return json.dumps({"trail": trail, "count": len(trail)})
    except Exception as exc:
        return json.dumps({"error": str(exc), "trail": [], "count": 0})

def register_graph() -> None:
    """Register graph domain tools (stub for v2 tooling framework)."""
    pass
