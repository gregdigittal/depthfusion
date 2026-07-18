"""depthfusion MCP tool implementations — capture domain."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from depthfusion.capture.event_hook import emit_if_high_importance
from depthfusion.core.types import ContextItem
from depthfusion.parsers import parse_conversation

try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

from depthfusion.mcp.tools._state import _get_context_bus, _get_hnsw_store

logger = logging.getLogger("depthfusion.mcp.server")


def _tool_tag_session(arguments: dict) -> str:
    session_id = arguments.get("session_id", "")
    tags = arguments.get("tags", [])
    return json.dumps({"session_id": session_id, "tags": tags, "tagged": True})

def _tool_publish_context(arguments: dict, config: Any = None) -> str:
    """Publish a context item to the bus with idempotent dedup (S-78).

    Returns JSON of the canonical publish-result shape:
      ``{"published": True, "item_id": <id>, "deduped": <bool>}``
    On dedup, ``item_id`` is the ORIGINAL stored item's id, not the retry's.
    """
    item_payload = arguments.get("item")
    if not isinstance(item_payload, dict):
        return json.dumps(
            {"error": "publish_context: 'item' must be an object", "published": False}
        )
    try:
        item = ContextItem(
            item_id=item_payload["item_id"],
            content=item_payload["content"],
            source_agent=item_payload["source_agent"],
            tags=list(item_payload.get("tags", [])),
            priority=item_payload.get("priority", "normal"),
            ttl_seconds=item_payload.get("ttl_seconds"),
            metadata=item_payload.get("metadata", {}),
            # S-70 — operator-supplied scoring (optional, defaults via
            # ContextItem.__post_init__). Unsupplied → canonical defaults.
            importance=item_payload.get("importance"),
            salience=item_payload.get("salience"),
            # S-112: structured observation fields (optional; default empty)
            facts=list(item_payload.get("facts") or []),
            concepts=list(item_payload.get("concepts") or []),
            files_read=list(item_payload.get("files_read") or []),
            files_modified=list(item_payload.get("files_modified") or []),
        )
    except (KeyError, TypeError) as exc:
        return json.dumps(
            {"error": f"publish_context: invalid item payload: {exc}", "published": False}
        )

    bus = _get_context_bus(config)
    try:
        result = bus.publish(item)
    except Exception as exc:  # noqa: BLE001 — surface bus errors verbatim
        return json.dumps(
            {"error": f"publish_context: bus error: {exc}", "published": False}
        )

    # S-73: emit event on first publish of a high-importance item (skip dedup retries)
    if isinstance(result, dict) and not result.get("deduped", False):
        _cfg = config if config is not None else type("_C", (), {
            "high_importance_threshold": 0.8,
            "event_log": "~/.claude/shared/depthfusion-events.jsonl",
        })()
        emit_if_high_importance(
            item,
            event_log=getattr(_cfg, "event_log", "~/.claude/shared/depthfusion-events.jsonl"),
            threshold=getattr(_cfg, "high_importance_threshold", 0.8),
        )

    # E-45: HNSW upsert behind feature flag; never blocks the BM25/bus path.
    indexed_in_hnsw = False
    store = _get_hnsw_store()
    if store is not None:
        try:
            indexed_in_hnsw = bool(store.upsert(item.item_id, item.content))
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.debug("[hnsw] upsert failed for %s: %s", item.item_id, exc)
            indexed_in_hnsw = False

    if isinstance(result, dict):
        result["indexed_in_hnsw"] = indexed_in_hnsw
    else:
        result = {"indexed_in_hnsw": indexed_in_hnsw, "result": result}
    return json.dumps(result)

def _tool_auto_learn(arguments: dict, config: Any = None) -> str:
    """Trigger auto-learn: session compression or ambient capture (S-110)."""
    mode = arguments.get("mode", "session")
    if mode == "ambient":
        return _handle_ambient_capture(arguments)

    from pathlib import Path
    max_files = min(int(arguments.get("max_files", 5)), 50)
    project = arguments.get("project", "")
    _sessions_override = os.environ.get("DEPTHFUSION_SESSIONS_DIR")
    sessions_dir = (
        Path(_sessions_override) if _sessions_override else Path.home() / ".claude" / "sessions"
    )
    if not sessions_dir.exists():
        return json.dumps({"compressed": 0, "message": "No sessions directory"})

    # S-74 fix: obtain graph_store once if graph extraction is enabled.
    # summarize_and_extract_graph is internally gated — safe to call always.
    graph_store = None
    if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true":
        try:
            from depthfusion.graph.store import get_store as _get_graph_store
            graph_store = _get_graph_store()
        except Exception:
            pass

    try:
        from depthfusion.capture.auto_learn import summarize_and_extract_graph
        from depthfusion.capture.compressor import SessionCompressor
        compressor = SessionCompressor()
        recent = sorted(sessions_dir.glob("*.tmp"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
        results = []
        for tmp in recent:
            out = compressor.compress(tmp)
            if out:
                results.append(str(out.name))
                summarize_and_extract_graph(out, project, graph_store)

        # S-229: maybe_trigger PersonaEngine after ingestion.
        _maybe_trigger_persona(
            scope={"project": project} if project else {},
            new_count=len(results),
            config=config,
        )

        return json.dumps({
            "compressed": len(results),
            "files": results,
            "message": f"Auto-learned from {len(results)} session files",
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "compressed": 0})

def _handle_ambient_capture(arguments: dict) -> str:
    """S-110: publish a low-importance ambient ContextItem to the FileBus."""
    tool_name = arguments.get("tool_name", "")
    session_id = arguments.get("session_id", "unknown")
    files_read = list(arguments.get("files_read") or [])
    files_modified = list(arguments.get("files_modified") or [])

    if not tool_name:
        return json.dumps({"error": "tool_name required for ambient mode", "published": False})

    try:
        from pathlib import Path

        from depthfusion.capture.auto_learn import build_ambient_item
        from depthfusion.router.bus import FileBus

        item = build_ambient_item(
            tool_name=tool_name,
            session_id=session_id,
            files_read=files_read,
            files_modified=files_modified,
        )
        bus_dir = Path(
            os.environ.get("DEPTHFUSION_BUS_FILE_DIR", "~/.claude/context-bus")
        ).expanduser()
        bus_dir.mkdir(parents=True, exist_ok=True)
        bus = FileBus(bus_dir=bus_dir)
        result = bus.publish(item)
        return json.dumps({"published": result.get("published", False), "item_id": item.item_id})
    except Exception as exc:
        return json.dumps({"error": str(exc), "published": False})

def _build_mermaid_canvas(
    session_id: str,
    output_path: str,
    *,
    max_tokens: int = 400,
) -> str:
    """Build a Mermaid task-canvas representing session state transitions.

    Reads the discovery file at *output_path* to extract state transitions
    (lines starting with ``#`` headings or ``-`` bullet prefixes).  Each
    transition becomes a Mermaid flowchart node.  The canvas is token-capped
    at *max_tokens* using a char-based heuristic (~4 chars/token): when the
    rendered diagram would exceed the cap, excess nodes are collapsed into a
    single ``overflow`` node.

    Parameters
    ----------
    session_id:
        Used as the Mermaid diagram title and in overflow node labels.
    output_path:
        Path to the compressed discovery file (used as source of state lines).
    max_tokens:
        Maximum token budget for the canvas (~4 chars/token).

    Returns
    -------
    str
        A Mermaid ``flowchart TD`` diagram string.
    """
    from pathlib import Path as _Path

    _CHARS_PER_TOKEN = 4
    max_chars = max_tokens * _CHARS_PER_TOKEN

    # Extract state lines from the discovery file.
    state_lines: list[str] = []
    try:
        text = _Path(output_path).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("- "):
                label = stripped.lstrip("#- ").strip()
                if label:
                    state_lines.append(label[:60])  # clamp node label length
    except OSError:
        pass

    if not state_lines:
        state_lines = ["session_start", "compress", "done"]

    # Build Mermaid header
    header = f"flowchart TD\n    title[\"{session_id} state canvas\"]\n"
    nodes: list[str] = []
    edges: list[str] = []

    prev_id = "title"
    for idx, label in enumerate(state_lines):
        safe_label = label.replace('"', "'")
        node_id = f"n{idx}"
        nodes.append(f'    {node_id}["{safe_label}"]')
        edges.append(f"    {prev_id} --> {node_id}")
        prev_id = node_id

    # Assemble and check size; collapse overflow nodes if needed
    def _assemble(n: list[str], e: list[str]) -> str:
        parts: list[str] = []
        if n:
            parts.append("\n".join(n))
        if e:
            parts.append("\n".join(e))
        return header + "\n".join(parts) + "\n"

    canvas = _assemble(nodes, edges)

    if len(canvas) > max_chars and len(nodes) > 1:
        overflow_node_template = '    overflow["… +{n} more nodes (token cap)"]'
        # Use worst-case node count for size estimation (overestimates slightly).
        worst_overflow_node = overflow_node_template.format(n=len(nodes))
        overflow_edge_template = "    {anchor} --> overflow"
        # Worst-case anchor is "title" (5 chars); any n-prefixed id is shorter.
        worst_overflow_edge = overflow_edge_template.format(anchor="title")
        overflow_chunk = len(worst_overflow_node) + 1 + len(worst_overflow_edge) + 1

        # Budget available for regular nodes + edges (excluding header and overflow).
        overflow_budget = max_chars - len(header) - overflow_chunk

        kept_nodes: list[str] = []
        kept_edges: list[str] = []
        running = 0
        cutoff = 0

        if overflow_budget > 0:
            for i, (nd, ed) in enumerate(zip(nodes, edges)):
                chunk = len(nd) + 1 + len(ed) + 1
                if running + chunk > overflow_budget:
                    cutoff = i
                    break
                kept_nodes.append(nd)
                kept_edges.append(ed)
                running += chunk
            else:
                cutoff = len(nodes)

        overflow_count = len(nodes) - cutoff
        if overflow_count > 0:
            last_kept_id = f"n{cutoff - 1}" if cutoff > 0 else "title"
            kept_nodes.append(overflow_node_template.format(n=overflow_count))
            kept_edges.append(overflow_edge_template.format(anchor=last_kept_id))

        canvas = _assemble(kept_nodes, kept_edges)

    # Hard clamp: if the canvas (even overflow-only) still exceeds max_chars,
    # return a minimal header-only string clamped to max_chars.  This occurs
    # when max_tokens is so small that header + overflow node > max_chars.
    # We truncate rather than exceed the budget.
    if len(canvas) > max_chars:
        canvas = canvas[:max_chars]

    return canvas


def _tool_compress_session(arguments: dict, config: object = None) -> str:
    """Compress a specific .tmp file into a discovery file.

    When ``offload_enabled`` is set on *config*, also produces a Mermaid
    task-canvas of session state transitions, returned in the response and
    stored alongside the discovery file.
    """
    from pathlib import Path
    session_path_str = arguments.get("session_path", "")
    if not session_path_str:
        return json.dumps({"error": "session_path argument required"})
    try:
        from depthfusion.capture.compressor import SessionCompressor
        compressor = SessionCompressor()
        out = compressor.compress(Path(session_path_str))
        if not out:
            return json.dumps({
                "success": False,
                "message": "Nothing to compress (empty or already done)",
            })

        # Base success response
        result: dict = {"success": True, "output": str(out)}

        # S-231: Mermaid canvas when offload_enabled
        offload_enabled = getattr(config, "offload_enabled", False)
        if offload_enabled:
            max_tokens = getattr(config, "offload_mmd_max_tokens", 400)
            session_id = Path(session_path_str).stem
            canvas = _build_mermaid_canvas(
                session_id=session_id,
                output_path=str(out),
                max_tokens=max_tokens,
            )
            # Store alongside discovery file
            canvas_path = out.with_suffix(".mmd")
            try:
                canvas_path.write_text(canvas, encoding="utf-8")
            except OSError as exc:
                logger.warning("[compress] failed to write canvas %s: %s", canvas_path, exc)
            result["mermaid_canvas"] = canvas
            result["canvas_path"] = str(canvas_path)

        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

def _tool_prune_discoveries(arguments: dict) -> str:
    """TG-14 / S-55: identify and optionally archive stale discovery files.

    Two-phase design:
      1. `confirm=False` (default) — return candidate list with reasons.
         No filesystem modification. Operator reviews the list.
      2. `confirm=True` — move listed candidates to
         `~/.claude/shared/discoveries/.archive/`. Never deletes.

    Arguments:
        age_days (int, optional): override the default 90-day threshold
            (or `DEPTHFUSION_PRUNE_AGE_DAYS` env var).
        confirm (bool, optional): when True, actually move the files.

    Returns:
        JSON with `candidates` (list of {path, reason, age_days}) and
        `moved` (list of archive paths, empty when confirm=False).
        On error, returns `{"ok": False, "error": "..."}`.
    """
    try:
        age_days_raw = arguments.get("age_days")
        age_days: int | None
        if age_days_raw is None:
            age_days = None
        else:
            age_days = int(age_days_raw)
            if age_days <= 0:
                return json.dumps({
                    "ok": False,
                    "error": f"age_days must be positive, got {age_days}",
                })
        confirm = bool(arguments.get("confirm", False))
    except (TypeError, ValueError) as exc:
        return json.dumps({"ok": False, "error": f"invalid arguments: {exc}"})

    try:
        from depthfusion.capture.pruner import (
            identify_candidates,
            prune_discoveries,
        )
        candidates = identify_candidates(age_days=age_days)
        candidates_json = [
            {
                "path": str(c.path),
                "reason": c.reason,
                "age_days": c.age_days,
            }
            for c in candidates
        ]

        if not confirm:
            return json.dumps({
                "ok": True,
                "candidates": candidates_json,
                "moved": [],
                "message": (
                    f"{len(candidates)} prune candidates identified. "
                    "Pass confirm=true to move them to "
                    "~/.claude/shared/discoveries/.archive/"
                ),
            }, indent=2)

        moved = prune_discoveries(candidates, confirm=True)
        return json.dumps({
            "ok": True,
            "candidates": candidates_json,
            "moved": [str(p) for p in moved],
            "message": f"Moved {len(moved)} file(s) to archive.",
        }, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})

def _tool_inspect_discovery(arguments: dict) -> str:
    """S-76: return parsed frontmatter of a discovery file."""
    import re as _re
    filename = arguments.get("filename", "")
    if not filename:
        return json.dumps({"error": "filename argument required", "exists": False})

    target = Path(os.path.expanduser(filename))
    if not target.exists():
        return json.dumps({"filename": str(target), "exists": False, "error": "file not found"})

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return json.dumps({"filename": str(target), "exists": True, "error": str(exc)})

    frontmatter: dict = {}
    fm_match = _re.match(r"^---\s*\n(.*?)\n---", text, _re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            kv = line.split(":", 1)
            if len(kv) == 2:
                key = kv[0].strip()
                val = kv[1].strip()
                # coerce common scalar types
                if val.lower() in ("true", "false"):
                    frontmatter[key] = val.lower() == "true"
                else:
                    try:
                        frontmatter[key] = float(val) if "." in val else int(val)
                    except ValueError:
                        frontmatter[key] = val

    return json.dumps({
        "filename": str(target),
        "exists": True,
        "frontmatter": frontmatter,
    }, indent=2)

def _tool_ingest_conversation(arguments: dict, config: Any = None) -> str:
    import hashlib
    import json

    provider = str(arguments.get("provider", "generic"))
    data = str(arguments.get("data", ""))
    if not data:
        return json.dumps(
            {"error": "data is required", "provider": provider, "fragments_stored": 0, "skipped": 0}
        )

    try:
        messages = parse_conversation(provider, data)
    except Exception as exc:
        return json.dumps({
            "error": str(exc),
            "provider": provider,
            "fragments_stored": 0,
            "skipped": 0,
        })

    fragments_stored = 0
    skipped = 0
    errors: list[str] = []
    for index, msg in enumerate(messages):
        if msg.get("role") not in ("assistant", "model"):
            skipped += 1
            continue
        content = str(msg.get("content", "")).strip()
        if len(content) < 20:
            skipped += 1
            continue
        try:
            item_payload = {
                "item_id": (
                    f"ingest:{provider}:{index}:"
                    + hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
                ),
                "content": content,
                "source_agent": "depthfusion_ingest_conversation",
                "tags": [f"provider:{provider}:ingested"],
                "metadata": {"sub_scope": f"provider:{provider}:ingested"},
            }
            publish_args = {"item": item_payload}
            _tool_publish_context(publish_args)
            fragments_stored += 1
        except Exception as exc:
            errors.append(str(exc))
            skipped += 1

    # S-229: maybe_trigger PersonaEngine after ingestion.
    _maybe_trigger_persona(
        scope={"project": provider},
        new_count=fragments_stored,
        config=config,
    )

    return json.dumps({
        "fragments_stored": fragments_stored,
        "skipped": skipped,
        "provider": provider,
        "errors": errors,
    })

def _maybe_trigger_persona(
    scope: dict,
    new_count: int,
    config: Any = None,
) -> None:
    """S-229: fire PersonaEngine.maybe_trigger when a persona engine is available.

    Errors are swallowed so persona generation never crashes capture tools.
    """
    try:
        from depthfusion.cognitive.persona import get_persona_engine
        engine = get_persona_engine()
        if engine is not None:
            engine.maybe_trigger(scope, new_count)
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.debug("PersonaEngine.maybe_trigger failed (suppressed): %s", exc)


def register_capture() -> None:
    """Register capture domain tools (stub for v2 tooling framework)."""
    pass
