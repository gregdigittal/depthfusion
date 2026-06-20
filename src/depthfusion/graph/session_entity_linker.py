# src/depthfusion/graph/session_entity_linker.py
"""VPS-side session entity linker.

Derives `SessionRecord` objects from existing event+memory entities already
in the graph store (instead of scanning local `.tmp` session files, which are
absent on the VPS because Claude Code runs on the developer's local machine).

Three public functions:
  get_sessions_from_events  — one-pass O(n) extraction
  get_unlinked_sessions     — filter to sessions with no PRECEDED_BY edges yet
  link_and_upsert           — run TemporalSessionLinker, write entities + edges

S-212 / Closes S-50 AC-3.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.linker import (
    SessionRecord,
    TemporalSessionLinker,
    make_edge_id,
    tokenize_session_content,
)
from depthfusion.graph.types import Entity

if TYPE_CHECKING:
    from depthfusion.graph.store import GraphStore

logger = logging.getLogger(__name__)


def get_sessions_from_events(graph_store: GraphStore) -> list[SessionRecord]:
    """Extract SessionRecord objects from event+memory entities in the graph.

    One O(n) pass over all entities:
    1. Build a `{entity_id → name}` index of all memory entities.
    2. For each event entity, look up its linked memory via
       `metadata["content_hash"]` and tokenize `memory.name` (first 80 chars
       of the original published context) plus `metadata["agent_id"]`.
    3. Group by `session_id`, accumulating vocabulary and keeping the earliest
       `first_seen` timestamp as the session timestamp.

    Returns a list of SessionRecord, one per distinct session_id found.
    Sessions with no vocabulary (e.g. events whose memory entity is missing)
    are silently skipped — they contribute nothing to temporal linking.
    """
    all_entities = graph_store.all_entities()

    # --- Step 1: index memory entities by entity_id (= content_hash) ----------
    memory_names: dict[str, str] = {}
    for ent in all_entities:
        if ent.type == "memory":
            memory_names[ent.entity_id] = ent.name

    # --- Step 2: accumulate vocabulary per session_id from event entities ------
    session_vocab: dict[str, set[str]] = {}
    session_ts: dict[str, str] = {}  # earliest first_seen per session
    session_project: dict[str, str] = {}

    for ent in all_entities:
        if ent.type != "event":
            continue
        meta = ent.metadata or {}
        session_id = meta.get("session_id")
        if not session_id:
            continue

        content_hash = meta.get("content_hash", "")
        agent_id = meta.get("agent_id", "")
        project = meta.get("project_slug") or ent.project or "unknown"

        # Build the vocabulary text from the memory's name + agent identifier.
        memory_name = memory_names.get(content_hash, "")
        vocab_text = f"{memory_name} {agent_id}"
        tokens = tokenize_session_content(vocab_text)
        if not tokens:
            continue

        if session_id not in session_vocab:
            session_vocab[session_id] = set()
            session_ts[session_id] = ent.first_seen
            session_project[session_id] = project
        else:
            # Keep the earliest timestamp for the session.
            if ent.first_seen < session_ts[session_id]:
                session_ts[session_id] = ent.first_seen

        session_vocab[session_id].update(tokens)

    records: list[SessionRecord] = [
        SessionRecord(
            session_id=sid,
            timestamp=session_ts[sid],
            vocabulary=session_vocab[sid],
            project=session_project[sid],
        )
        for sid in session_vocab
        if session_vocab[sid]  # skip sessions that ended up with empty vocab
    ]

    logger.info(
        "[session_linker] extracted %d session records from %d entities",
        len(records),
        len(all_entities),
    )
    return records


def get_unlinked_sessions(graph_store: GraphStore) -> list[SessionRecord]:
    """Return only sessions that have no outgoing PRECEDED_BY edges yet.

    Each session's entity_id is derived via `make_entity_id(session_id,
    "session", project)`.  If no session entity exists in the graph yet, the
    session is also considered unlinked (it will be created by `link_and_upsert`).
    """
    all_sessions = get_sessions_from_events(graph_store)
    if not all_sessions:
        return []

    unlinked: list[SessionRecord] = []
    for rec in all_sessions:
        entity_id = make_entity_id(rec.session_id, "session", rec.project)
        edges = graph_store.get_edges(entity_id, relationship_filter=["PRECEDED_BY"])
        if not edges:
            unlinked.append(rec)

    logger.info(
        "[session_linker] %d/%d sessions unlinked",
        len(unlinked),
        len(all_sessions),
    )
    return unlinked


def link_and_upsert(
    sessions: list[SessionRecord],
    graph_store: GraphStore,
    *,
    dry_run: bool = False,
) -> dict:
    """Run TemporalSessionLinker over `sessions`, upsert entities + edges.

    Parameters
    ----------
    sessions:
        Output of `get_sessions_from_events()` or `get_unlinked_sessions()`.
    graph_store:
        Live GraphStore instance — must support upsert_entity / upsert_edge.
    dry_run:
        When True, compute what would be written but write nothing.

    Returns
    -------
    dict with keys ``sessions``, ``edges_added``, ``dry_run``.
    """
    if not sessions:
        return {"sessions": 0, "edges_added": 0, "dry_run": dry_run}

    window_hours = float(
        os.getenv("DEPTHFUSION_SESSION_WINDOW_HOURS", "168.0")
    )
    min_overlap = int(
        os.getenv("DEPTHFUSION_SESSION_MIN_OVERLAP", "5")
    )
    linker = TemporalSessionLinker(
        window_hours=window_hours,
        min_overlap=min_overlap,
    )

    edges = linker.link_all(sessions)
    logger.info(
        "[session_linker] %d PRECEDED_BY edges from %d sessions (dry_run=%s)",
        len(edges),
        len(sessions),
        dry_run,
    )

    if dry_run:
        return {"sessions": len(sessions), "edges_added": len(edges), "dry_run": True}

    # Upsert session entities first so edge foreign keys resolve.
    for rec in sessions:
        entity_id = make_entity_id(rec.session_id, "session", rec.project)
        graph_store.upsert_entity(Entity(
            entity_id=entity_id,
            name=rec.session_id,
            type="session",
            project=rec.project,
            source_files=[rec.session_id],
            confidence=1.0,
            first_seen=rec.timestamp,
            metadata={
                "vocabulary_size": len(rec.vocabulary),
                "acl_allow": [rec.project],
            },
        ))

    # Remap edge source/target from raw session_id to entity_id form.
    edges_added = 0
    for edge in edges:
        project_a = next(
            (r.project for r in sessions if r.session_id == edge.source_id),
            "unknown",
        )
        project_b = next(
            (r.project for r in sessions if r.session_id == edge.target_id),
            "unknown",
        )
        src_entity = make_entity_id(edge.source_id, "session", project_a)
        tgt_entity = make_entity_id(edge.target_id, "session", project_b)
        new_edge_id = make_edge_id(src_entity, tgt_entity, edge.relationship)
        edge.source_id = src_entity
        edge.target_id = tgt_entity
        edge.edge_id = new_edge_id
        if not edge.metadata.get("acl_allow"):
            edge.metadata["acl_allow"] = [project_a]
        graph_store.upsert_edge(edge)
        edges_added += 1

    logger.info(
        "[session_linker] upserted %d session entities, %d PRECEDED_BY edges",
        len(sessions),
        edges_added,
    )
    return {"sessions": len(sessions), "edges_added": edges_added, "dry_run": False}
